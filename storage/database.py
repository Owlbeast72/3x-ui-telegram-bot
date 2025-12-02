# storage/database.py
import os
import json
from datetime import datetime
from typing import Optional, List
from config import BOT_TOKEN
from sqlalchemy import create_engine, Column, String, Boolean, ForeignKey, Integer
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.future import select

# ---------- Настройки ----------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "bot.db")
DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

# Создаём папку data
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# ---------- Модели ----------
Base = declarative_base()

class User(Base):
    __tablename__ = "users"

    tg_id = Column(String, primary_key=True, unique=True, nullable=False)
    username = Column(String(32), nullable=True)
    first_name = Column(String(64), nullable=False)
    trial_days_left = Column(Integer, default=0, nullable=False)
    notify_expiry = Column(Boolean, default=True)
    notify_traffic = Column(Boolean, default=True)

    # === НОВЫЕ ПОЛЯ ДЛЯ СКИДОК ===
    pending_discount_type = Column(String, nullable=True)  # "percent" или "fixed_rub"
    pending_discount_value = Column(Integer, nullable=True)  # 10 или 100

    created_at = Column(String, default=lambda: datetime.utcnow().isoformat())

class Server(Base):
    __tablename__ = "servers"

    id = Column(String, primary_key=True)
    country = Column(String)
    city = Column(String)
    xui_url = Column(String)
    xui_username = Column(String)
    xui_password = Column(String)
    inbound_id = Column(String)
    mobile_spoof = Column(Boolean, default=False)
    subscription_path = Column(String, default="/sub")
    subscription_port = Column(String, default="2096")
    active = Column(Boolean, default=True)

class Config(Base):
    __tablename__ = "configs"

    id = Column(String, primary_key=True)
    user_tg_id = Column(String, ForeignKey("users.tg_id"))
    server_id = Column(String, ForeignKey("servers.id"))
    client_email = Column(String)
    base_tariff = Column(String)
    traffic_limit_gb = Column(String)
    traffic_used_bytes = Column(String, default="0")
    expiry = Column(String)
    created_at = Column(String)
    last_traffic_reset = Column(String, nullable=True) # ISO datetime или null
    vless_link = Column(String)
    subscription_link = Column(String)
    client_sub_id = Column(String)
    active = Column(Boolean, default=True)
    addons = Column(String, default='{"extra_traffic_gb": 0, "traffic_reset_count": 0}')
    notify_expiry_sent = Column(Boolean, default=False)
    notify_traffic_80_sent = Column(Boolean, default=False)
    notify_traffic_95_sent = Column(Boolean, default=False)

class PendingPayment(Base):
    __tablename__ = "pending_payments"

    payment_id = Column(String, primary_key=True)
    bot_invoice_id = Column(String)
    payload = Column(String)
    created_at = Column(String)
    user_id = Column(String, ForeignKey("users.tg_id"))

class Tariff(Base):
    __tablename__ = "tariffs"

    id = Column(Integer, primary_key=True)
    category = Column(String, nullable=False)      # "mobile", "stable"
    price_rub = Column(Integer, nullable=False)
    duration_days = Column(Integer, nullable=False)
    traffic_gb = Column(Integer, default=100)
    active = Column(Boolean, default=True)

class Promocode(Base):
    __tablename__ = "promocodes"

    id = Column(Integer, primary_key=True)  # ← добавьте autoincrement=False
    code_hash = Column(String, unique=True, nullable=False)
    code = Column(String, unique=True, nullable=False)
    discount_type = Column(String, nullable=False)      # "fixed_days", "percent", "fixed_rub"
    discount_value = Column(Integer, nullable=False)    # 3, 20, 100
    max_uses = Column(Integer, default=1)
    used_count = Column(Integer, default=0)
    valid_until = Column(String)  # ISO datetime или null
    applies_to_categories = Column(String)  # JSON: ["mobile", "stable"] или "all"
    active = Column(Boolean, default=True)

class PromoUsage(Base):
    __tablename__ = "promo_usage"

    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False)
    promo_code_hash = Column(String, ForeignKey("promocodes.code_hash"), nullable=False)  # ← ссылка на code_hash
    used_at = Column(String, nullable=False)

# ---------- Сессия ----------
engine = create_async_engine(DATABASE_URL, echo=False)
async_session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# ---------- Инициализация ----------
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# ---------- Утилиты (аналог json_storage) ----------
async def get_user_configs(tg_id: str) -> List[Config]:
    async with async_session_maker() as session:
        result = await session.execute(select(Config).where(Config.user_tg_id == tg_id))
        return result.scalars().all()

async def get_or_create_user(tg_id: str, **kwargs) -> User:
    async with async_session_maker() as session:
        result = await session.execute(select(User).where(User.tg_id == tg_id))
        user = result.scalar_one_or_none()
        if not user:
            user = User(tg_id=tg_id, **kwargs)
            session.add(user)
            await session.commit()
        return user

async def get_active_servers() -> List[Server]:
    async with async_session_maker() as session:
        result = await session.execute(select(Server).where(Server.active == True))
        return result.scalars().all()

# ... другие функции по мере необходимости
async_engine = engine
