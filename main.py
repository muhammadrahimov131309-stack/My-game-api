import os
import asyncpg
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

DATABASE_URL = os.getenv("DATABASE_URL")
pool = None

@app.on_event("startup")
async def startup():
    global pool
    if not DATABASE_URL:
        print("ОШИБКА: DATABASE_URL не найден!")
        return
    dsn = DATABASE_URL.replace("?sslmode=require", "?ssl=true")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
    print("✅ Подключение к базе установлено!")

@app.on_event("shutdown")
async def shutdown():
    if pool: await pool.close()

@app.get("/")
async def root(): return {"status": "ok"}

@app.get("/api/profile/{tg_id}")
async def get_profile(tg_id: int):
    if not pool: raise HTTPException(500, "DB not connected")
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT nickname, balance, created_at FROM users WHERE tg_id = $1", tg_id)
        if not user: raise HTTPException(404, "User not found")
        biz_count = await conn.fetchval("SELECT COUNT(*) FROM businesses WHERE user_id = $1", tg_id)
        return {"tg_id": tg_id, "nickname": user["nickname"], "balance": user["balance"], "created_at": str(user["created_at"]), "businesses": biz_count}

@app.get("/api/health")
async def health(): return {"status": "healthy"}