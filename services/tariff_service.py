# services/tariff_service.py
from storage.database import async_session_maker, Tariff
from sqlalchemy import select, distinct

async def get_tariff_categories():
    """Возвращает список уникальных категорий тарифов."""
    async with async_session_maker() as session:
        result = await session.execute(
            select(distinct(Tariff.category)).where(Tariff.active == True)
        )
        return [row[0] for row in result.fetchall()]

async def get_all_tariffs():
    async with async_session_maker() as session:
        result = await session.execute(Tariff.__table__.select())
        return result.fetchall()

async def create_tariff(category: str, duration_days: int, price_rub: int, traffic_gb: int):
    async with async_session_maker() as session:
        await session.execute(Tariff.__table__.insert().values(
            category=category,
            duration_days=duration_days,
            price_rub=price_rub,
            traffic_gb=traffic_gb,
            active=True
        ))
        await session.commit()

async def get_tariffs_by_category(category: str):
    """Возвращает тарифы по категории."""
    async with async_session_maker() as session:
        result = await session.execute(
            select(Tariff)
            .where(Tariff.category == category, Tariff.active == True)
            .order_by(Tariff.duration_days)
        )
        return result.scalars().all()

async def initialize_default_tariffs():
    async with async_session_maker() as session:
        count = await session.execute("SELECT COUNT(*) FROM tariffs")
        if count.scalar() == 0:
            default_tariffs = [
                # Mobile
                {"category": "mobile", "price_rub": 150, "duration_days": 7, "traffic_gb": 100},
                {"category": "mobile", "price_rub": 600, "duration_days": 30, "traffic_gb": 100},
                # Stable
                {"category": "stable", "price_rub": 100, "duration_days": 7, "traffic_gb": 100},
                {"category": "stable", "price_rub": 400, "duration_days": 30, "traffic_gb": 100},
                {"category": "stable", "price_rub": 1100, "duration_days": 90, "traffic_gb": 150},
            ]
            for t in default_tariffs:
                await session.execute(Tariff.__table__.insert().values(**t))
            await session.commit()
