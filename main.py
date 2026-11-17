import os
import asyncio
import logging
import random
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton

# ==========================================
# ⚙️ ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ (Твои настройки)
# ==========================================
DATABASE_URL = os.getenv("DATABASE_URL")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7127705775"))
MINI_APP_URL = os.getenv("MINI_APP_URL", "https://your-app.pages.dev")
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "https://my-game-api-jm79.onrender.com")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Проверка токена
if not BOT_TOKEN:
    logger.warning("⚠️ Внимание: BOT_TOKEN не задан! Бот не сможет запуститься, пока вы не укажете его в настройках Render.")

# ==========================================
# 🗄 БАЗА ДАННЫХ (Временная в памяти для быстрого старта)
# ==========================================
users_db = {}

def get_or_create_user(tg_id: int, username: str = "Боец"):
    if tg_id not in users_db:
        users_db[tg_id] = {
            "id": len(users_db) + 1,
            "tg_id": tg_id,
            "nickname": username,
            "balance": 10000,          # Стартовый бонус по ТЗ
            "level": 1,
            "xp": 450,
            "max_xp": 1000,
            "gang": "SHADOW",          # Как на твоем скриншоте
            "rank": len(users_db) + 1,
            "daily_day": 3,
            "daily_claimed": False,
            "slot_spins": 1,
            "inventory": [
                {"id": 1, "name": "Кепка новичка", "type": "accessory", "rarity": "common", "price": 1000},
                {"id": 2, "name": "Белая футболка", "type": "skin", "rarity": "common", "price": 2500},
                {"id": 3, "name": "Тренч SHADOW", "type": "skin", "rarity": "epic", "price": 50000},
            ],
            "force_win": None,         # 'win' / 'lose' / None (подкрутка из админки)
            "is_banned": False
        }
    return users_db[tg_id]

# ==========================================
# 🤖 TELEGRAM БОТ (AIOGRAM 3)
# ==========================================
bot = Bot(token=BOT_TOKEN) if BOT_TOKEN else None
dp = Dispatcher()

if bot:
    @dp.message(CommandStart())
    async def cmd_start(message: types.Message):
        user = get_or_create_user(message.from_user.id, message.from_user.first_name)
        if user["is_banned"]:
            return await message.answer("🚫 Ваш аккаунт заблокирован администрацией.")

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎮 Открыть TSTCOIN", web_app=WebAppInfo(url=MINI_APP_URL))]
        ])
        await message.answer(
            f"👋 Привет, <b>{user['nickname']}</b>!\n\n"
            f"Добро пожаловать в <b>TSTCOIN</b>.\n"
            f"💰 Твой баланс: <b>{user['balance']:,}</b> монет\n"
            f"🏴 Банда: <b>{user['gang']}</b>\n\n"
            f"Жми кнопку ниже, чтобы войти в игру 👇",
            reply_markup=kb,
            parse_mode="HTML"
        )

    # --- 🛡 АДМИН-ПАНЕЛЬ В ТЕЛЕГРАМЕ ---
    @dp.message(Command("admin"))
    async def cmd_admin(message: types.Message):
        if message.from_user.id != ADMIN_ID:
            return
        
        text = (
            "🛡 <b>АДМИН-ПАНЕЛЬ TSTCOIN</b>\n\n"
            "Команды управления:\n"
            "• <code>/give [tg_id] [сумма]</code> — выдать монеты\n"
            "• <code>/setwin [tg_id] win</code> — подкрутить победу\n"
            "• <code>/setwin [tg_id] lose</code> — подкрутить слив\n"
            "• <code>/ban [tg_id]</code> — заблокировать игрока\n"
            f"\nАктивных игроков в базе: <b>{len(users_db)}</b>"
        )
        await message.answer(text, parse_mode="HTML")

    @dp.message(Command("give"))
    async def admin_give(message: types.Message):
        if message.from_user.id != ADMIN_ID:
            return
        try:
            _, target_id, amount = message.text.split()
            target_id, amount = int(target_id), int(amount)
            user = get_or_create_user(target_id)
            user["balance"] += amount
            await message.answer(f"✅ Игроку <code>{target_id}</code> начислено <b>{amount:,}</b> монет.", parse_mode="HTML")
        except Exception:
            await message.answer("❌ Ошибка. Пример: <code>/give 7127705775 50000</code>", parse_mode="HTML")

    @dp.message(Command("setwin"))
    async def admin_setwin(message: types.Message):
        if message.from_user.id != ADMIN_ID:
            return
        try:
            _, target_id, mode = message.text.split()
            target_id = int(target_id)
            user = get_or_create_user(target_id)
            user["force_win"] = mode if mode in ["win", "lose"] else None
            await message.answer(f"🎲 Подкрутка для <code>{target_id}</code>: <b>{user['force_win']}</b>", parse_mode="HTML")
        except Exception:
            await message.answer("❌ Ошибка. Пример: <code>/setwin 7127705775 win</code>", parse_mode="HTML")


# ==========================================
# 🚀 FASTAPI СЕРВЕР И ФОНОВЫЙ ЗАПУСК БОТА
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    polling_task = None
    if bot:
        # Запускаем бота в фоне, чтобы FastAPI успел открыть порт для Render
        polling_task = asyncio.create_task(dp.start_polling(bot))
        logger.info("🤖 Telegram бот успешно запущен в фоновом режиме!")
    else:
        logger.warning("⚠️ Бот не запущен, так как отсутствует BOT_TOKEN.")
    
    yield
    
    if polling_task:
        polling_task.cancel()
        if bot:
            await bot.session.close()
        logger.info("🛑 Бот остановлен.")

app = FastAPI(title="TSTCOIN API", lifespan=lifespan)

# Разрешаем CORS, чтобы Mini App на Vercel/Netlify мог общаться с бэкендом
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# 🌐 API ЭНДПОИНТЫ ДЛЯ MINI APP
# ==========================================
class UserInitRequest(BaseModel):
    tg_id: int
    username: Optional[str] = "Боец"

class BetRequest(BaseModel):
    tg_id: int
    amount: int
    multiplier: float = 2.0

@app.get("/")
async def health_check():
    """Эндпоинт для проверки здоровья (Render и UptimeRobot пингуют его)"""
    return {
        "status": "ok", 
        "game": "TSTCOIN", 
        "render_url": RENDER_URL,
        "active_users": len(users_db)
    }

@app.post("/api/user/init")
async def api_user_init(data: UserInitRequest):
    """Инициализация профиля игрока в Mini App (убирает экран 'Загрузка...')"""
    user = get_or_create_user(data.tg_id, data.username)
    if user["is_banned"]:
        raise HTTPException(status_code=403, detail="Аккаунт заблокирован")
    return user

@app.post("/api/daily/claim")
async def api_daily_claim(data: UserInitRequest):
    """Сбор ежедневной награды"""
    user = get_or_create_user(data.tg_id)
    if user["daily_claimed"]:
        raise HTTPException(status_code=400, detail="Уже получено сегодня")
    
    reward = 25000
    user["balance"] += reward
    user["daily_claimed"] = True
    user["xp"] += 150
    return {"status": "success", "reward": reward, "new_balance": user["balance"]}

@app.post("/api/casino/play")
async def api_casino_play(bet: BetRequest):
    """Игровая механика с учетом админской подкрутки"""
    user = get_or_create_user(bet.tg_id)
    if bet.amount <= 0 or bet.amount > user["balance"]:
        raise HTTPException(status_code=400, detail="Недостаточно монет")

    user["balance"] -= bet.amount

    # Проверка подкрутки из админки
    if user["force_win"] == "win":
        is_win = True
        user["force_win"] = None  # Сбрасываем одноразовую подкрутку
    elif user["force_win"] == "lose":
        is_win = False
        user["force_win"] = None
    else:
        # Стандартный шанс с небольшим преимуществом казино
        win_chance = (100.0 / bet.multiplier) * 0.95
        is_win = random.uniform(0, 100) <= win_chance

    payout = int(bet.amount * bet.multiplier) if is_win else 0
    if is_win:
        user["balance"] += payout
        user["xp"] += 50

    return {
        "win": is_win,
        "payout": payout,
        "new_balance": user["balance"]
    }

