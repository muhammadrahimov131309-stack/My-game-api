import os
import random
import asyncio
import asyncpg
from datetime import date, timedelta
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
BOT_TOKEN = os.getenv("BOT_TOKEN")
MINI_APP_URL = os.getenv("MINI_APP_URL", "https://your-app.pages.dev")

pool = None
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

class Reg(StatesGroup):
    waiting_nickname = State()

# --- БАЗА ДАННЫХ ---
async def init_db():
    global pool
    dsn = DATABASE_URL.replace("?sslmode=require", "?ssl=true")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                tg_id BIGINT PRIMARY KEY, nickname TEXT UNIQUE, balance BIGINT DEFAULT 10000, 
                level INT DEFAULT 1, xp INT DEFAULT 0, gang_id INT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS inventory (
                id SERIAL PRIMARY KEY, tg_id BIGINT, item_id INT, rarity TEXT, acquired_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS daily_rewards (
                tg_id BIGINT PRIMARY KEY, last_claim DATE, streak INT DEFAULT 0
            );
        """)

async def get_user_db(tg_id):
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE tg_id = $1", tg_id)

# ==========================================
# ЧАСТЬ 1: ЛОГИКА БОТА (AIOGRAM)
# ==========================================

def main_menu_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="💰 Баланс")],
        [KeyboardButton(text="🎮 Открыть игру (Mini App)", web_app={"url": MINI_APP_URL})]
    ], resize_keyboard=True)

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    user = await get_user_db(message.from_user.id)
    if user:
        await message.answer(f"С возвращением, {user['nickname']}! Твой баланс: {user['balance']} 💰", reply_markup=main_menu_kb())
    else:
        await state.set_state(Reg.waiting_nickname)
        await message.answer("Привет! Добро пожаловать в UNDERWORLD.\n\nКак тебя зовут, боец? Напиши свой ник:")

@dp.message(Reg.waiting_nickname)
async def process_nickname(message: Message, state: FSMContext):
    nickname = message.text.strip()
    if len(nickname) < 2 or len(nickname) > 20:
        await message.answer("Ник должен быть от 2 до 20 символов. Попробуй ещё:")
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute("INSERT INTO users (tg_id, nickname) VALUES ($1, $2)", message.from_user.id, nickname)
        await state.clear()
        await message.answer(f"Добро пожаловать, {nickname}! Ты получаешь 10 000 монет 💰", reply_markup=main_menu_kb())
    except asyncpg.UniqueViolationError:
        await message.answer("Этот ник уже занят. Придумай другой:")

@dp.message(F.text == "👤 Профиль")
async def bot_profile(message: Message):
    user = await get_user_db(message.from_user.id)
    if not user: return await message.answer("Напиши /start")
    await message.answer(f"👤 Ник: {user['nickname']}\n💰 Баланс: {user['balance']}\n📈 Уровень: {user['level']}")

@dp.message(F.text == "💰 Баланс")
async def bot_balance(message: Message):
    user = await get_user_db(message.from_user.id)
    if user: await message.answer(f"💰 Баланс: {user['balance']} монет")

# ==========================================
# ЧАСТЬ 2: API ДЛЯ MINI APP (FASTAPI)
# ==========================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    asyncio.create_task(dp.start_polling(bot))
    yield
    await bot.session.close()
    await pool.close()

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.get("/api/profile/{tg_id}")
async def api_profile(tg_id: int):
    user = await get_user_db(tg_id)
    if not user: raise HTTPException(404, "Игрок не найден")
    async with pool.acquire() as conn:
        rank = await conn.fetchval("SELECT COUNT(*) FROM users WHERE balance > $1", user['balance']) + 1
    return {"nickname": user['nickname'], "balance": user['balance'], "rank": rank}

@app.post("/api/daily/{tg_id}")
async def api_daily(tg_id: int):
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT last_claim, streak FROM daily_rewards WHERE tg_id = $1", tg_id)
        today = date.today()
        if row and row['last_claim'] == today:
            raise HTTPException(400, "Уже забрал сегодня!")
        
        streak = 1
        if row and row['last_claim'] == today - timedelta(days=1):
            streak = row['streak'] + 1
            
        reward = 5000 * streak
        await conn.execute("UPDATE users SET balance = balance + $1 WHERE tg_id = $2", reward, tg_id)
        await conn.execute("""
            INSERT INTO daily_rewards (tg_id, last_claim, streak) VALUES ($1, $2, $3)
            ON CONFLICT (tg_id) DO UPDATE SET last_claim = $2, streak = $3
        """, tg_id, today, streak)
        
        new_bal = await conn.fetchval("SELECT balance FROM users WHERE tg_id = $1", tg_id)
        return {"status": "ok", "reward": reward, "new_balance": new_bal}

class UpgRequest(BaseModel):
    tg_id: int
    bet: int
    mult: float

@app.post("/api/casino/upgrader")
async def api_upgrader(req: UpgRequest):
    async with pool.acquire() as conn:
        bal = await conn.fetchval("SELECT balance FROM users WHERE tg_id = $1", req.tg_id)
        if bal < req.bet: raise HTTPException(400, "Мало денег!")
        
        chance = round(90 / req.mult)
        roll = random.randint(1, 100)
        win = 0
        if roll <= chance:
            win = int(req.bet * (req.mult - 1))
            await conn.execute("UPDATE users SET balance = balance + $1 WHERE tg_id = $2", win, req.tg_id)
        else:
            await conn.execute("UPDATE users SET balance = balance - $1 WHERE tg_id = $2", req.bet, req.tg_id)
        
        new_bal = await conn.fetchval("SELECT balance FROM users WHERE tg_id = $1", req.tg_id)
        return {"result": "win" if win > 0 else "lose", "roll": roll, "new_balance": new_bal}
