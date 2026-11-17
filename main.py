import os
import random
import asyncio
import asyncpg
from datetime import date, timedelta
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, Update
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7127705775"))
MINI_APP_URL = os.getenv("MINI_APP_URL", "https://your-app.pages.dev")
# Ссылка на твой сервис на Render (без слэша на конце)
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "https://my-game-api-jm79.onrender.com")

pool = None
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

class Reg(StatesGroup):
    waiting_nickname = State()

async def init_db():
    global pool
    dsn = DATABASE_URL.replace("?sslmode=require", "?ssl=true")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                tg_id BIGINT PRIMARY KEY, nickname TEXT UNIQUE, balance BIGINT DEFAULT 10000, 
                level INT DEFAULT 1, xp INT DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS daily_rewards (
                tg_id BIGINT PRIMARY KEY, last_claim DATE, streak INT DEFAULT 0
            );
        """)

def main_menu_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="💰 Баланс")],
        [KeyboardButton(text="🎮 Открыть игру", web_app={"url": MINI_APP_URL})]
    ], resize_keyboard=True)

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE tg_id = $1", message.from_user.id)
    
    if user:
        await message.answer(f"С возвращением, {user['nickname']}! Баланс: {user['balance']} 💰", reply_markup=main_menu_kb())
    else:
        await state.set_state(Reg.waiting_nickname)
        await message.answer("Привет! Как тебя зовут, боец? Напиши свой ник:")

@dp.message(Reg.waiting_nickname)
async def process_nickname(message: Message, state: FSMContext):
    nickname = message.text.strip()
    if len(nickname) < 2:
        await message.answer("Слишком коротко. Напиши ник:")
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
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE tg_id = $1", message.from_user.id)
    if not user: return await message.answer("Напиши /start")
    await message.answer(f"👤 Ник: {user['nickname']}\n💰 Баланс: {user['balance']}\n📈 Уровень: {user['level']}")

@dp.message(F.text == "💰 Баланс")
async def bot_balance(message: Message):
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT balance FROM users WHERE tg_id = $1", message.from_user.id)
    if user: await message.answer(f"💰 Баланс: {user['balance']} монет")

# --- API ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # Устанавливаем вебхук для бота
    webhook_url = f"{RENDER_URL}/webhook"
    await bot.set_webhook(webhook_url, drop_pending_updates=True)
    yield
    await bot.session.close()
    await pool.close()

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update(**data)
    await dp.feed_update(bot=bot, update=update)
    return {"ok": True}

@app.get("/api/profile/{tg_id}")
async def api_profile(tg_id: int):
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT nickname, balance, level FROM users WHERE tg_id = $1", tg_id)
        if not user: raise HTTPException(404, "Игрок не найден")
        rank = await conn.fetchval("SELECT COUNT(*) FROM users WHERE balance > $1", user['balance']) + 1
    return {"nickname": user['nickname'], "balance": user['balance'], "level": user['level'], "rank": rank}

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
        return {"reward": reward, "new_balance": new_bal}
