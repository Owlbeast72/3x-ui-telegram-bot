import hashlib
from datetime import datetime, timezone
from storage.database import async_session_maker, Promocode, User, PromoUsage
from sqlalchemy import select, text

async def get_all_promocodes():
    async with async_session_maker() as session:
        result = await session.execute(select(Promocode))
        return result.fetchall()

def _generate_code_hash(code: str) -> str:
    """Генерирует стабильный хеш из кода промокода."""
    return hashlib.md5(code.upper().encode()).hexdigest()

async def create_promocode(code: str, discount_type: str, discount_value: int, max_uses: int):
    code_upper = code.upper()
    code_hash = hashlib.md5(code_upper.encode()).hexdigest()
    
    async with async_session_maker() as session:
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
        await session.execute(
            Promocode.__table__.update()
            .where(Promocode.id == promo_id)
            .values(active=active)
        )
        await session.commit()

async def delete_promo(promo_code_hash: str):
    async with async_session_maker() as session:
        await session.execute(
            Promocode.__table__.delete().where(Promocode.code_hash == promo_code_hash)
        )
        await session.commit()

async def apply_promocode(user_id: str, code: str) -> dict:
    code_upper = code.upper()
    async with async_session_maker() as session:
        # Ищем промокод по code
        result = await session.execute(
            select(Promocode).where(
                Promocode.code == code_upper,
                Promocode.active == True,
                Promocode.used_count < Promocode.max_uses
            )
        )
        promo = result.scalar_one_or_none()
        if not promo:
            return {"success": False, "message": "❌ Промокод недействителен..."}

        # Проверяем использование по code_hash
        usage_result = await session.execute(
            select(PromoUsage).where(
                PromoUsage.user_id == user_id,
                PromoUsage.promo_code_hash == promo.code_hash  # ← используем хеш
            )
        )
        if usage_result.scalar_one_or_none():
            return {"success": False, "message": "❌ Вы уже использовали этот промокод."}

        # === Гарантируем, что пользователь существует ===
        user_result = await session.execute(select(User).where(User.tg_id == user_id))
        user = user_result.scalar_one_or_none()
        if not user:
            # Создаём нового пользователя
            new_user = User(
                tg_id=user_id,
                username="",
                first_name="Anonymous"
            )
            session.add(new_user)
            await session.flush()  # Получаем ID, если нужно

        # === Применяем эффект промокода ===
        if promo.discount_type == "fixed_days":
            # Обновляем trial_days_left (работает и для новых, и для старых)
            await session.execute(
                User.__table__.update()
                .where(User.tg_id == user_id)
                .values(trial_days_left=User.trial_days_left + promo.discount_value)
            )
            from utils.helpers import format_duration_human
            duration_text = format_duration_human(promo.discount_value)
            message = f"✅ Получено {duration_text} бесплатной подписки!"
        else:
            # Сохраняем скидку в профиль
            await session.execute(
                User.__table__.update()
                .where(User.tg_id == user_id)
                .values(
                    pending_discount_type=promo.discount_type,
                    pending_discount_value=promo.discount_value
                )
            )
            if promo.discount_type == "percent":
                message = f"✅ Промокод применён! Скидка: {promo.discount_value}%"
            else:
                message = f"✅ Промокод применён! Скидка: {promo.discount_value} ₽"

        # === Фиксируем использование ===
        new_usage = PromoUsage(
            user_id=user_id,
            promo_code_hash=promo.code_hash,  # ← сохраняем хеш
            used_at=datetime.now(timezone.utc).isoformat()
        )
        session.add(new_usage)

        # Увеличиваем счётчик использований промокода
        await session.execute(
            Promocode.__table__.update()
            .where(Promocode.id == promo.id)
            .values(used_count=Promocode.used_count + 1)
        )
        
        await session.commit()
        return {"success": True, "message": message}
