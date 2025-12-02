# services/promocode_service.py
import hashlib
from datetime import datetime, timezone
from sqlalchemy import select
from storage.database import async_session_maker, Promocode, User, PromoUsage

# Опционально: импортируем только если используется
try:
    from utils.helpers import format_duration_human
except ImportError:
    def format_duration_human(days: int) -> str:
        # Простая fallback-реализация на случай отсутствия helpers
        if days % 10 == 1 and days % 100 != 11:
            return f"{days} день"
        elif 2 <= days % 10 <= 4 and not (10 <= days % 100 <= 20):
            return f"{days} дня"
        else:
            return f"{days} дней"


def _generate_code_hash(code: str) -> str:
    """Генерирует стабильный хеш из кода промокода (всегда в верхнем регистре)."""
    return hashlib.md5(code.upper().encode()).hexdigest()


async def get_all_promocodes():
    async with async_session_maker() as session:
        result = await session.execute(select(Promocode))
        return result.scalars().all()


async def create_promocode(code: str, discount_type: str, discount_value: int, max_uses: int):
    code_upper = code.upper()
    code_hash = _generate_code_hash(code)

    async with async_session_maker() as session:
        # Проверка на дубликат (опционально, но полезно)
        existing = await session.execute(select(Promocode).where(Promocode.code_hash == code_hash))
        if existing.scalar_one_or_none():
            raise ValueError(f"Промокод с кодом '{code}' уже существует.")

        promo = Promocode(
            code=code_upper,
            code_hash=code_hash,
            discount_type=discount_type,
            discount_value=discount_value,
            max_uses=max_uses,
            used_count=0,
            active=True
        )
        session.add(promo)
        await session.commit()


async def toggle_promo_status(promo_id: int, active: bool):
    async with async_session_maker() as session:
        result = await session.execute(select(Promocode).where(Promocode.id == promo_id))
        promo = result.scalar_one_or_none()
        if promo:
            promo.active = active
            await session.commit()


async def delete_promo(promo_code_hash: str):
    """Удаляет промокод по его code_hash."""
    async with async_session_maker() as session:
        result = await session.execute(select(Promocode).where(Promocode.code_hash == promo_code_hash))
        promo = result.scalar_one_or_none()
        if promo:
            await session.delete(promo)
            await session.commit()


async def apply_promocode(user_id: str, code: str) -> dict:
    code_upper = code.upper()
    code_hash = _generate_code_hash(code)

    async with async_session_maker() as session:
        # Ищем активный промокод по коду
        result = await session.execute(
            select(Promocode).where(
                Promocode.code == code_upper,
                Promocode.active.is_(True),
                Promocode.used_count < Promocode.max_uses
            )
        )
        promo = result.scalar_one_or_none()
        if not promo:
            return {"success": False, "message": "❌ Промокод недействителен или исчерпан."}

        # Проверяем, не использовал ли пользователь уже этот промокод (по хешу)
        usage_check = await session.execute(
            select(PromoUsage).where(
                PromoUsage.user_id == user_id,
                PromoUsage.promo_code_hash == promo.code_hash
            )
        )
        if usage_check.scalar_one_or_none():
            return {"success": False, "message": "❌ Вы уже использовали этот промокод."}

        # Гарантируем существование пользователя
        user_result = await session.execute(select(User).where(User.tg_id == user_id))
        user = user_result.scalar_one_or_none()
        if not user:
            user = User(tg_id=user_id, first_name="Anonymous", username="")
            session.add(user)
            await session.flush()

        # Применяем эффект
        if promo.discount_type == "fixed_days":
            user.trial_days_left = (user.trial_days_left or 0) + promo.discount_value
            duration_text = format_duration_human(promo.discount_value)
            message = f"✅ Получено {duration_text} бесплатной подписки!"
        else:
            user.pending_discount_type = promo.discount_type
            user.pending_discount_value = promo.discount_value
            if promo.discount_type == "percent":
                message = f"✅ Промокод применён! Скидка: {promo.discount_value}%"
            else:
                message = f"✅ Промокод применён! Скидка: {promo.discount_value} ₽"

        # Фиксируем использование
        session.add(PromoUsage(
            user_id=user_id,
            promo_code_hash=promo.code_hash,
            used_at=datetime.now(timezone.utc).isoformat()
        ))

        # Увеличиваем счётчик
        promo.used_count += 1

        await session.commit()
        return {"success": True, "message": message}
