# utils/helpers.py
import random
import string
from datetime import datetime, timezone
from typing import Union, Optional

from config import STABLE_BASE_PRICES, MOBILE_BASE_PRICES

# Маппинг дней → код тарифа
DAYS_TO_TARIFF_CODE = {
    7: "1w",
    30: "1m",
    60: "2m",
    90: "3m",
    180: "6m",
    365: "1y"
}

def format_duration_human(days: int) -> str:
    if days == 1:
        return "1 день"
    elif days == 2:
        return "2 дня"
    elif days == 3:
        return "3 дня"
    elif days == 4:
        return "4 дня"
    else:
        return f"{days} дней"

def format_tariff_name(duration_days: int) -> str:
    if 1 <= duration_days <= 4:
        return f"{duration_days} день(дней)"
    elif 5 <= duration_days <= 7:
        return "1 неделя"
    elif 8 <= duration_days <= 28:
        weeks = duration_days // 7
        return f"{weeks} неделя(недель)"
    elif 29 <= duration_days <= 31:
        return "1 месяц"
    elif 32 <= duration_days <= 120:
        months = duration_days // 30
        return f"{months} месяца"
    else:
        return f"{duration_days // 30}+ месяцев"

def generate_random_prefix(length: int = 16) -> str:
    """
    Генерирует случайную строку из букв и цифр.
    
    Args:
        length: Длина строки (по умолчанию 16)
    
    Returns:
        Случайная строка
    """
    if length <= 0:
        raise ValueError("Длина должна быть положительным числом")
    
    chars = string.ascii_letters + string.digits
    return ''.join(random.choices(chars, k=length))

def gb_to_bytes(gb: Union[int, float]) -> int:
    """
    Конвертирует гигабайты в байты.
    
    Args:
        gb: Количество гигабайт
    
    Returns:
        Количество байт
    """
    return int(gb * (1024 ** 3))

def bytes_to_gb(bytes_value: int) -> float:
    """Конвертирует байты в гигабайты."""
    return bytes_value / (1024 ** 3)

async def get_next_config_number(user_id: str) -> int:
    """
    Возвращает номер следующего конфига для пользователя.
    Используется для генерации email и комментариев.
    
    Args:
        user_id: Telegram ID пользователя (строка)
    
    Returns:
        Номер следующего конфига (начиная с 1)
    """
    from storage.database import async_session_maker, Config
    
    async with async_session_maker() as session:
        result = await session.execute(
            Config.__table__.select().where(Config.user_tg_id == user_id)
        )
        configs = result.fetchall()
        return len(configs) + 1
