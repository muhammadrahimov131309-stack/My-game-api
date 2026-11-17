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
# ⚙️ ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ
# ==========================================
DATABASE_URL = os.getenv("DATABASE_URL")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7127705775"))
MINI_APP_URL = os.getenv("MINI_APP_URL", "https://your-app.pages.dev")
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "https://my-game-api-jm79.onrender.com")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==========================================
# 🗄 БАЗА ДАННЫХ И ДЕФОЛТНЫЕ ДАННЫЕ (В памяти)
# ==========================================
users_db = {}

# Магазин по умолчанию (товары)
SHOP_ITEMS = [
    {"id": 1, "name": "Кепка новичка", "type": "accessory", "rarity": "common", "price": 1000},
    {"id": 2, "name": "Белая футболка", "type": "skin", "rarity": "common", "price": 2500},
    {"id": 3, "name": "Тренч SHADOW", "type": "skin", "rarity": "epic", "price": 50000},
    {"id": 4, "name": "Спортивная тачка", "type": "car", "rarity": "legendary", "price": 500000},
]

def get_or_create_user(tg_id: int, username: str = "Боец"):
    if tg_id not in users_db:
        users_db[tg_id] = {
            "id": len(users_db) + 1,
            "tg_id": tg_id,
            "nickname": username,
            "balance": 10000,          # Стартовый капитал
            "level": 1,
            "xp": 450,
            "max_xp": 1000,
            "gang": "SHADOW",
            "rank": len(users_db) + 1,
            "daily_claimed": False,
            "inventory": [SHOP_ITEMS[0], SHOP_ITEMS[1]],
            "businesses": [
                {"biz_id": 1, "name": "Палатка с хот-догами", "level": 1, "income_per_hour": 500, "accumulated": 1200}
            ],
            "force_win": None,
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
            return await message.answer("🚫 Ваш аккаунт заблокирован.")

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎮 Открыть TSTCOIN", web_app=WebAppInfo(url=MINI_APP_URL))]
        ])
        await message.answer(
            f"👋 Привет, <b>{user['nickname']}</b>!\n\n"
            f"💰 Баланс: <b>{user['balance']:,}</b> монет\n"
            f"🏴 Банда: <b>{user['gang']}</b>",
            reply_markup=kb,
            parse_mode="HTML"
        )

    @dp.message(Command("admin"))
    async def cmd_admin(message: types.Message):
        if message.from_user.id != ADMIN_ID:
            return
        await message.answer("🛡 Админ-панель активна. Используйте /give и /setwin.")

    @dp.message(Command("give"))
    async def admin_give(message: types.Message):
        if message.from_user.id != ADMIN_ID:
            return
        try:
            _, target_id, amount = message.text.split()
            user = get_or_create_user(int(target_id))
            user["balance"] += int(amount)
            await message.answer(f"✅ Начислено {amount} игроку {target_id}.")
        except Exception:
            await message.answer("❌ Формат: `/give tg_id сумма`")

    @dp.message(Command("setwin"))
    async def admin_setwin(message: types.Message):
        if message.from_user.id != ADMIN_ID:
            return
        try:
            _, target_id, mode = message.text.split()
            user = get_or_create_user(int(target_id))
            user["force_win"] = mode if mode in ["win", "lose"] else None
            await message.answer(f"🎲 Подкрутка для {target_id}: {user['force_win']}")
        except Exception:
            await message.answer("❌ Формат: `/setwin tg_id win` (или lose)")

# ==========================================
# 🚀 LIFESPAN & FASTAPI
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    polling_task = None
    if bot:
        polling_task = asyncio.create_task(dp.start_polling(bot))
    yield
    if polling_task:
        polling_task.cancel()
        if bot:
            await bot.session.close()

app = FastAPI(title="TSTCOIN API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# 🌐 API ЭНДПОИНТЫ ПОД SWAGGER
# ==========================================

@app.get("/")
async def health_check():
    return {"status": "ok", "game": "TSTCOIN", "active_users": len(users_db)}

# 1. Получить профиль
@app.get("/api/profile/{tg_id}")
async def get_profile(tg_id: int):
    user = get_or_create_user(tg_id)
    if user["is_banned"]:
        raise HTTPException(status_code=403, detail="Заблокирован")
    return user

# 2. Ежедневная награда
@app.post("/api/daily/{tg_id}")
async def claim_daily(tg_id: int):
    user = get_or_create_user(tg_id)
    if user["daily_claimed"]:
        raise HTTPException(status_code=400, detail="Уже получено сегодня")
    
    reward = 25000
    user["balance"] += reward
    user["daily_claimed"] = True
    return {"status": "success", "reward": reward, "new_balance": user["balance"]}

# 3. Получить доступ к магазину
@app.get("/api/магазин")
async def get_shop():
    return {"items": SHOP_ITEMS}

# 4. Купить товар в магазине
class BuyRequest(BaseModel):
    tg_id: int
    item_id: int

@app.post("/api/магазин/купить")
async def buy_item(data: BuyRequest):
    user = get_or_create_user(data.tg_id)
    item = next((i for i in SHOP_ITEMS if i["id"] == data.item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Товар не найден")
    if user["balance"] < item["price"]:
        raise HTTPException(status_code=400, detail="Недостаточно средств")
    
    user["balance"] -= item["price"]
    user["inventory"].append(item)
    return {"status": "success", "new_balance": user["balance"], "item": item}

# 5. Получить инвентарь
@app.get("/api/инвентарь/{tg_id}")
async def get_inventory(tg_id: int):
    user = get_or_create_user(tg_id)
    return {"inventory": user["inventory"]}

# 6. Игра в Апгрейдер казино
class UpgraderRequest(BaseModel):
    tg_id: int
    amount: int
    multiplier: float = 2.0

@app.post("/api/casino/upgrader")
async def casino_upgrader(bet: UpgraderRequest):
    user = get_or_create_user(bet.tg_id)
    if bet.amount <= 0 or user["balance"] < bet.amount:
        raise HTTPException(status_code=400, detail="Неверная ставка")
    
    user["balance"] -= bet.amount

    # Проверка подкрутки
    if user["force_win"] == "win":
        is_win = True
        user["force_win"] = None
    elif user["force_win"] == "lose":
        is_win = False
        user["force_win"] = None
    else:
        win_chance = (100.0 / bet.multiplier) * 0.95
        is_win = random.uniform(0, 100) <= win_chance

    payout = int(bet.amount * bet.multiplier) if is_win else 0
    if is_win:
        user["balance"] += payout

    return {"win": is_win, "payout": payout, "new_balance": user["balance"]}

# 7. Получить бизнесы
@app.get("/api/businesses/{tg_id}")
async def get_businesses(tg_id: int):
    user = get_or_create_user(tg_id)
    return {"businesses": user["businesses"]}

# 8. Собирать доход с бизнеса
@app.post("/api/businesses/collect/{biz_id}")
async def collect_business(biz_id: int, data: dict):
    # Ожидаем tg_id в теле запроса
    tg_id = data.get("tg_id")
    if not tg_id:
        raise HTTPException(status_code=400, detail="Укажите tg_id")
    user = get_or_create_user(int(tg_id))
    
    biz = next((b for b in user["businesses"] if b["biz_id"] == biz_id), None)
    if not biz:
        raise HTTPException(status_code=404, detail="Бизнес не найден")
    
    earned = biz["accumulated"]
    if earned <= 0:
        raise HTTPException(status_code=400, detail="Собирать пока нечего")
    
    user["balance"] += earned
    biz["accumulated"] = 0
    return {"status": "success", "collected": earned, "new_balance": user["balance"]}

# 9. Рейтинг
@app.get("/api/рэйтинг")
@app.get("/api/рейтинг")
async def get_rating():
    # Возвращаем простой топ игроков на основе нашей базы
    sorted_users = sorted(users_db.values(), key=lambda x: x["balance"], reverse=True)
    leaderboard = [
        {"rank": idx + 1, "nickname": u["nickname"], "balance": u["balance"], "gang": u["gang"]}
        for idx, u in enumerate(sorted_users[:10])
    ]
    return {"leaderboard": leaderboard}

