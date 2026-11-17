import os
import random
import asyncio
import asyncpg
from datetime import date, datetime, timedelta
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7127705775"))
MINI_APP_URL = os.getenv("MINI_APP_URL", "https://your-app.pages.dev")

pool = None
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- СОСТОЯНИЯ ---
class Reg(StatesGroup):
    waiting_nickname = State()

class AdminStates(StatesGroup):
    waiting_id = State()
    waiting_amount = State()
    waiting_reason = State()
    waiting_broadcast = State()

# --- ИНИЦИАЛИЗАЦИЯ БАЗЫ ---
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
            CREATE TABLE IF NOT EXISTS bans (
                tg_id BIGINT PRIMARY KEY, reason TEXT, banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

# ==========================================
# ЧАСТЬ 1: TELEGRAM БОТ
# ==========================================

def main_menu_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="💰 Баланс")],
        [KeyboardButton(text="🎮 Открыть игру (Mini App)", web_app={"url": MINI_APP_URL})]
    ], resize_keyboard=True)

# --- Мидлварь для проверки бана ---
from aiogram import BaseMiddleware
class BanMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        uid = None
        if isinstance(event, Message): uid = event.from_user.id
        elif isinstance(event, CallbackQuery): uid = event.from_user.id
        
        if uid and uid != ADMIN_ID:
            async with pool.acquire() as conn:
                ban = await conn.fetchrow("SELECT reason FROM bans WHERE tg_id = $1", uid)
            if ban:
                reason = ban['reason'] or "без причины"
                if isinstance(event, Message):
                    await event.answer(f"🚫 Ты забанен.\nПричина: {reason}")
                else:
                    await event.answer(f"🚫 Забанен: {reason}", show_alert=True)
                return
        return await handler(event, data)

dp.message.outer_middleware(BanMiddleware())
dp.callback_query.outer_middleware(BanMiddleware())

# --- Старт ---
@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE tg_id = $1", message.from_user.id)
    
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
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE tg_id = $1", message.from_user.id)
    if not user: return await message.answer("Напиши /start")
    await message.answer(f"👤 Ник: {user['nickname']}\n💰 Баланс: {user['balance']}\n📈 Уровень: {user['level']}")

# ==========================================
# ЧАСТЬ 2: АДМИН-ПАНЕЛЬ (ТОЛЬКО ДЛЯ ADMIN_ID)
# ==========================================

def admin_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Найти игрока", callback_data="adm_find")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="adm_stats")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="adm_broadcast")],
        [InlineKeyboardButton(text="🧯 Очистить чат", callback_data="adm_clear")]
    ])

@dp.message(Command("admin"))
async def admin_cmd(message: Message):
    if message.from_user.id != ADMIN_ID: return
    await message.answer("🛡 <b>АДМИН-ПАНЕЛЬ</b>\nВыбери действие:", parse_mode="HTML", reply_markup=admin_menu_kb())

@dp.callback_query(F.data == "adm_stats")
async def adm_stats(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    async with pool.acquire() as conn:
        users = await conn.fetchval("SELECT COUNT(*) FROM users")
        total_money = await conn.fetchval("SELECT SUM(balance) FROM users")
        bans = await conn.fetchval("SELECT COUNT(*) FROM bans")
    
    text = (f"📊 <b>Статистика</b>\n\n"
            f"👥 Игроков: {users}\n"
            f"💰 Всего денег: {total_money or 0:,}\n"
            f"🚫 Забанено: {bans}")
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="adm_back")]]))
    await callback.answer()

# --- Поиск игрока (упрощенно) ---
@dp.callback_query(F.data == "adm_find")
async def adm_find(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await state.set_state(AdminStates.waiting_id)
    await callback.message.edit_text("Введи Telegram ID игрока или ник (например: 7127705775):")
    await callback.answer()

@dp.message(AdminStates.waiting_id)
async def adm_find_process(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    query = message.text.strip()
    
    async with pool.acquire() as conn:
        if query.isdigit():
            user = await conn.fetchrow("SELECT * FROM users WHERE tg_id = $1", int(query))
        else:
            user = await conn.fetchrow("SELECT * FROM users WHERE LOWER(nickname) = LOWER($1)", query)
    
    if not user:
        await message.answer("❌ Игрок не найден")
        await state.clear()
        return
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Изменить баланс", callback_data=f"adm_bal_{user['tg_id']}")],
        [InlineKeyboardButton(text="🚫 Забанить/Разбанить", callback_data=f"adm_ban_{user['tg_id']}")],
        [InlineKeyboardButton(text="🎯 Форс-вин (игра)", callback_data=f"adm_win_{user['tg_id']}")]
    ])
    
    await message.answer(f"👤 <b>{user['nickname']}</b> (ID: {user['tg_id']})\n💰 Баланс: {user['balance']:,}", parse_mode="HTML", reply_markup=kb)
    await state.clear()

# --- Изменение баланса ---
@dp.callback_query(F.data.startswith("adm_bal_"))
async def adm_bal(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    uid = int(callback.data.split("_")[2])
    await state.update_data(target_uid=uid)
    await state.set_state(AdminStates.waiting_amount)
    await callback.message.edit_text("Введи сумму (положительную для выдачи, отрицательную для списания):")
    await callback.answer()

@dp.message(AdminStates.waiting_amount)
async def adm_bal_process(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    try:
        amount = int(message.text.strip())
    except ValueError:
        return await message.answer("Введи число!")
    
    data = await state.get_data()
    uid = data['target_uid']
    
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET balance = balance + $1 WHERE tg_id = $2", amount, uid)
    
    await message.answer(f"✅ Баланс игрока {uid} изменен на {amount:,}", parse_mode="HTML", reply_markup=main_menu_kb())
    await state.clear()

# --- Бан ---
@dp.callback_query(F.data.startswith("adm_ban_"))
async def adm_ban(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    uid = int(callback.data.split("_")[2])
    
    async with pool.acquire() as conn:
        ban = await conn.fetchrow("SELECT reason FROM bans WHERE tg_id = $1", uid)
        if ban:
            await conn.execute("DELETE FROM bans WHERE tg_id = $1", uid)
            await callback.message.edit_text(f"✅ Игрок {uid} разбанен.")
        else:
            await conn.execute("INSERT INTO bans (tg_id, reason) VALUES ($1, 'Нарушение правил')", uid)
            await callback.message.edit_text(f"✅ Игрок {uid} забанен.")
    await callback.answer()

# --- Форс-вин (заглушка) ---
@dp.callback_query(F.data.startswith("adm_win_"))
async def adm_win(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    await callback.answer("Форс-вин активирован на следующую игру (заглушка)", show_alert=True)

# --- Рассылка ---
@dp.callback_query(F.data == "adm_broadcast")
async def adm_broadcast(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await state.set_state(AdminStates.waiting_broadcast)
    await callback.message.edit_text("📢 Введи текст для рассылки всем игрокам:")
    await callback.answer()

@dp.message(AdminStates.waiting_broadcast)
async def adm_broadcast_process(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    text = message.text.strip()
    
    async with pool.acquire() as conn:
        users = await conn.fetch("SELECT tg_id FROM users")
    
    sent = 0
    for user in users:
        try:
            await bot.send_message(user['tg_id'], text)
            sent += 1
            await asyncio.sleep(0.05)
        except:
            pass
    
    await message.answer(f"✅ Рассылка завершена. Отправлено: {sent}/{len(users)}")
    await state.clear()

@dp.callback_query(F.data == "adm_back")
async def adm_back(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await state.clear()
    await callback.message.edit_text("🛡 <b>АДМИН-ПАНЕЛЬ</b>\nВыбери действие:", parse_mode="HTML", reply_markup=admin_menu_kb())
    await callback.answer()

# ==========================================
# ЧАСТЬ 3: API ДЛЯ MINI APP
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
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT nickname, balance FROM users WHERE tg_id = $1", tg_id)
        if not user: raise HTTPException(404, "Игрок не найден")
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
        return {"reward": reward, "new_balance": new_bal}

class UpgReq(BaseModel):
    tg_id: int
    bet: int
    mult: float

@app.post("/api/casino/upgrader")
async def api_upg(req: UpgReq):
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
        return {"result": "win" if win > 0 else "lose", "new_balance": new_bal}
