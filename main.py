import os
import asyncpg
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

DATABASE_URL = os.getenv("DATABASE_URL")
pool = None

# Наш магазин (перенесли сюда из bot.py)
SHOP_ITEMS = [
    {"id": 1,  "name": "Кепка новичка",     "type": "accessory", "rarity": "common",    "price": 5000, "emoji": "🟢"},
    {"id": 2,  "name": "Крутая цепочка",    "type": "accessory", "rarity": "rare",      "price": 50000, "emoji": "🟦"},
    {"id": 3,  "name": "Золотые часы",      "type": "accessory", "rarity": "epic",      "price": 500000, "emoji": "🟣"},
    {"id": 5,  "name": "Джинсы + футболка", "type": "skin",      "rarity": "common",    "price": 10000, "emoji": "🟢"},
    {"id": 9,  "name": "Лада Приора",       "type": "car",       "rarity": "common",    "price": 200000, "emoji": "🟢"},
    {"id": 10, "name": "BMW M5",            "type": "car",       "rarity": "rare",      "price": 2000000, "emoji": "🟦"},
]

@app.on_event("startup")
async def startup():
    global pool
    if not DATABASE_URL: return
    dsn = DATABASE_URL.replace("?sslmode=require", "?ssl=true")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
    print("✅ API запущено!")

@app.on_event("shutdown")
async def shutdown():
    if pool: await pool.close()

@app.get("/")
async def root(): return {"status": "ok"}

@app.get("/api/profile/{tg_id}")
async def get_profile(tg_id: int):
    if not pool: raise HTTPException(500, "DB not connected")
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT nickname, balance FROM users WHERE tg_id = $1", tg_id)
        if not user: raise HTTPException(404, "User not found")
        return {"tg_id": tg_id, "nickname": user["nickname"], "balance": user["balance"]}

@app.get("/api/shop")
async def get_shop():
    return {"items": SHOP_ITEMS}

# Модель для запроса покупки
class BuyRequest(BaseModel):
    tg_id: int
    item_id: int

@app.post("/api/shop/buy")
async def buy_item(req: BuyRequest):
    if not pool: raise HTTPException(500, "DB error")
    item = next((i for i in SHOP_ITEMS if i["id"] == req.item_id), None)
    if not item: raise HTTPException(404, "Item not found")

    async with pool.acquire() as conn:
        # Проверяем баланс
        user = await conn.fetchrow("SELECT balance FROM users WHERE tg_id = $1", req.tg_id)
        if not user: raise HTTPException(404, "User not found")
        if user["balance"] < item["price"]:
            raise HTTPException(400, "Недостаточно монет!")

        # Списываем деньги и добавляем в инвентарь
        await conn.execute("UPDATE users SET balance = balance - $1 WHERE tg_id = $2", item["price"], req.tg_id)
        await conn.execute("INSERT INTO inventory (user_id, item_id) VALUES ($1, $2)", req.tg_id, item["id"])
        
        new_balance = await conn.fetchval("SELECT balance FROM users WHERE tg_id = $1", req.tg_id)
        
    return {"status": "success", "message": f"Куплено: {item['name']}", "new_balance": new_balance}
