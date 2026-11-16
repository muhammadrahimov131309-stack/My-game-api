import os
import random
import asyncpg
from datetime import date, datetime, timedelta
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

DATABASE_URL = os.getenv("DATABASE_URL")
pool = None

# --- КОНСТАНТЫ ИГРЫ ---
SHOP_ITEMS = {
    1: {"name": "Кепка новичка", "price": 5000, "emoji": "🧢"},
    2: {"name": "Золотые часы", "price": 50000, "emoji": "⌚"},
    3: {"name": "Лада Приора", "price": 200000, "emoji": "🚗"},
    4: {"name": "BMW M5", "price": 2000000, "emoji": "🏎️"},
    5: {"name": "Lamborghini", "price": 15000000, "emoji": "🔥"},
}

BIZ_TYPES = {
    "tent": {"name": "Палатка", "price": 50000, "income": 1000, "max": 10},
    "shop": {"name": "Магазин", "price": 250000, "income": 6000, "max": 15},
}

# --- БАЗА ДАННЫХ ---
@app.on_event("startup")
async def startup():
    global pool
    dsn = DATABASE_URL.replace("?sslmode=require", "?ssl=true")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
    async with pool.acquire() as conn:
        # Автосоздание таблиц при первом запуске
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                tg_id BIGINT PRIMARY KEY, nickname TEXT, balance BIGINT DEFAULT 10000, 
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS inventory (
                id SERIAL PRIMARY KEY, tg_id BIGINT, item_id INT
            );
            CREATE TABLE IF NOT EXISTS daily (
                tg_id BIGINT PRIMARY KEY, last_claim DATE, streak INT DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS businesses (
                id SERIAL PRIMARY KEY, tg_id BIGINT, type TEXT, level INT DEFAULT 1, last_collect TIMESTAMP
            );
        """)

@app.on_event("shutdown")
async def shutdown():
    if pool: await pool.close()

# --- УТИЛИТЫ ---
def fmt(n): return f"{n:,}".replace(",", " ")

# --- ЭНДПОИНТЫ ПРОФИЛЯ ---
@app.get("/api/profile/{tg_id}")
async def get_profile(tg_id: int):
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT nickname, balance FROM users WHERE tg_id = $1", tg_id)
        if not user: raise HTTPException(404, "Игрок не найден в базе")
        
        # Считаем место в топе
        rank = await conn.fetchval("SELECT COUNT(*) FROM users WHERE balance > $1", user["balance"]) + 1
        return {"tg_id": tg_id, "nickname": user["nickname"], "balance": user["balance"], "rank": rank}

# --- ДЕЙЛИКИ ---
@app.post("/api/daily/{tg_id}")
async def claim_daily(tg_id: int):
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT last_claim, streak FROM daily WHERE tg_id = $1", tg_id)
        today = date.today()
        
        if row and row["last_claim"] == today:
            raise HTTPException(400, "Ты уже забирал награду сегодня!")
        
        streak = 1
        if row and row["last_claim"] == today - timedelta(days=1):
            streak = row["streak"] + 1
            
        reward = 5000 * streak
        await conn.execute("UPDATE users SET balance = balance + $1 WHERE tg_id = $2", reward, tg_id)
        await conn.execute("""
            INSERT INTO daily (tg_id, last_claim, streak) VALUES ($1, $2, $3)
            ON CONFLICT (tg_id) DO UPDATE SET last_claim = $2, streak = $3
        """, tg_id, today, streak)
        
        new_bal = await conn.fetchval("SELECT balance FROM users WHERE tg_id = $1", tg_id)
        return {"status": "ok", "reward": reward, "streak": streak, "new_balance": new_bal}

# --- МАГАЗИН И ИНВЕНТАРЬ ---
@app.get("/api/shop")
async def get_shop():
    return {"items": [{"id": k, **v} for k, v in SHOP_ITEMS.items()]}

class BuyRequest(BaseModel):
    tg_id: int
    item_id: int

@app.post("/api/shop/buy")
async def buy_item(req: BuyRequest):
    item = SHOP_ITEMS.get(req.item_id)
    if not item: raise HTTPException(404, "Товар не найден")
    async with pool.acquire() as conn:
        bal = await conn.fetchval("SELECT balance FROM users WHERE tg_id = $1", req.tg_id)
        if bal < item["price"]: raise HTTPException(400, "Недостаточно монет!")
        
        await conn.execute("UPDATE users SET balance = balance - $1 WHERE tg_id = $2", item["price"], req.tg_id)
        await conn.execute("INSERT INTO inventory (tg_id, item_id) VALUES ($1, $2)", req.tg_id, req.item_id)
        new_bal = await conn.fetchval("SELECT balance FROM users WHERE tg_id = $1", req.tg_id)
        return {"status": "ok", "new_balance": new_bal, "message": f"Куплено: {item['name']}"}

@app.get("/api/inventory/{tg_id}")
async def get_inventory(tg_id: int):
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, item_id FROM inventory WHERE tg_id = $1", tg_id)
        return {"items": [{"inv_id": r["id"], **SHOP_ITEMS[r["item_id"]]} for r in rows]}

# --- КАЗИНО (АПГРЕЙДЕР) ---
class UpgRequest(BaseModel):
    tg_id: int
    bet: int
    mult: float

@app.post("/api/casino/upgrader")
async def play_upg(req: UpgRequest):
    async with pool.acquire() as conn:
        bal = await conn.fetchval("SELECT balance FROM users WHERE tg_id = $1", req.tg_id)
        if bal < req.bet or req.bet < 100: raise HTTPException(400, "Ошибка ставки!")
        
        chance = round(90 / req.mult)
        roll = random.randint(1, 100)
        
        if roll <= chance:
            win = int(req.bet * (req.mult - 1))
            await conn.execute("UPDATE users SET balance = balance + $1 WHERE tg_id = $2", win, req.tg_id)
            res = "win"
        else:
            await conn.execute("UPDATE users SET balance = balance - $1 WHERE tg_id = $2", req.bet, req.tg_id)
            res = "lose"
            
        new_bal = await conn.fetchval("SELECT balance FROM users WHERE tg_id = $1", req.tg_id)
        return {"result": res, "roll": roll, "chance": chance, "new_balance": new_bal}

# --- РАБОТА: БИЗНЕСЫ ---
@app.get("/api/businesses/{tg_id}")
async def get_bizes(tg_id: int):
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, type, level, last_collect FROM businesses WHERE tg_id = $1", tg_id)
        res = []
        for r in rows:
            biz = BIZ_TYPES[r["type"]]
            # Считаем доход
            sec_passed = (datetime.now() - r["last_collect"]).total_seconds()
            max_sec = 24 * 3600
            eff_sec = min(sec_passed, max_sec)
            income = int((biz["income"] * r["level"] / 3600) * eff_sec)
            
            res.append({
                "id": r["id"], "name": biz["name"], "level": r["level"], 
                "income_per_hour": biz["income"] * r["level"], "pending": income
            })
        return {"businesses": res}

@app.post("/api/businesses/collect/{biz_id}")
async def collect_biz(biz_id: int):
    async with pool.acquire() as conn:
        r = await conn.fetchrow("SELECT tg_id, type, level, last_collect FROM businesses WHERE id = $1", biz_id)
        if not r: raise HTTPException(404, "Не найдено")
        
        biz = BIZ_TYPES[r["type"]]
        sec_passed = (datetime.now() - r["last_collect"]).total_seconds()
        income = int((biz["income"] * r["level"] / 3600) * min(sec_passed, 86400))
        
        if income > 0:
            await conn.execute("UPDATE users SET balance = balance + $1 WHERE tg_id = $2", income, r["tg_id"])
            await conn.execute("UPDATE businesses SET last_collect = $1 WHERE id = $2", datetime.now(), biz_id)
            
        new_bal = await conn.fetchval("SELECT balance FROM users WHERE tg_id = $1", r["tg_id"])
        return {"status": "ok", "collected": income, "new_balance": new_bal}

# --- РЕЙТИНГ ---
@app.get("/api/rating")
async def get_rating():
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT nickname, balance FROM users ORDER BY balance DESC LIMIT 10")
        return {"top": [{"nickname": r["nickname"], "balance": r["balance"]} for r in rows]}  
