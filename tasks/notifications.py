# tasks/notifications.py
import asyncio
import logging
import random
from datetime import datetime, timezone, timedelta
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import select
from storage.database import async_session_maker, Config, User, Server
from services.xui_manager import XUIManager

logger = logging.getLogger(__name__)

# Интервал: 3 часа ± до 10 минут
BASE_INTERVAL = 3 * 3600  # 10800 сек
JITTER_RANGE = 600        # ±600 сек = ±10 мин


async def send_subscription_notifications(bot: Bot):
    while True:
        try:
            logger.info("🔍 Проверка уведомлений об истечении подписки...")
            now = datetime.now(timezone.utc)
            warning_threshold = now + timedelta(days=3)

            # === ШАГ 1: Получаем и копируем данные ===
            notifications_data = []
            async with async_session_maker() as session:
                query = (
                    select(Config, Server)
                    .join(Server, Config.server_id == Server.id)
                    .where(
                        Config.active == True,
                        Config.expiry > now.isoformat(),
                        Config.expiry <= warning_threshold.isoformat(),
                        Config.notify_expiry_sent == False
                    )
                )
                for config, server in (await session.execute(query)).all():
                    notifications_data.append({
                        "user_tg_id": config.user_tg_id,
                        "config_id": config.id,
                        "expiry": config.expiry,
                        "server_country": server.country,
                        "server_city": server.city
                    })

                expired_query = (
                    select(Config, Server)
                    .join(Server, Config.server_id == Server.id)
                    .where(
                        Config.active == True,
                        Config.expiry <= now.isoformat(),
                        Config.expiry > (now - timedelta(days=1)).isoformat(),
                        Config.notify_expiry_sent == False
                    )
                )
                for config, server in (await session.execute(expired_query)).all():
                    notifications_data.append({
                        "user_tg_id": config.user_tg_id,
                        "config_id": config.id,
                        "expiry": config.expiry,
                        "server_country": server.country,
                        "server_city": server.city
                    })

            # === ШАГ 2: Обрабатываем данные ВНЕ сессии ===
            for item in notifications_data:
                user = await _get_notifying_user(item["user_tg_id"], notify_expiry=True)
                if not user:
                    continue

                try:
                    expiry_dt = datetime.fromisoformat(item["expiry"].replace("Z", "+00:00"))
                    days_left = max(0, (expiry_dt - now).days)
                    short_id = item["config_id"][:7] + "..." if len(item["config_id"]) > 7 else item["config_id"]
                    server_name = f"{item['server_country']} ({item['server_city']})"

                    kb = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🔄 Продлить", callback_data=f"renew_menu_{item['config_id']}")],
                        [InlineKeyboardButton(text="✅ Понятно", callback_data=f"notification_ok_{item['config_id']}")]
                    ])

                    if days_left > 0:
                        text = f"⚠️ Подписка на <b>{server_name}</b> (<code>{short_id}</code>) истекает через <b>{days_left} дн.</b>"
                    else:
                        text = f"❌ Подписка на <b>{server_name}</b> (<code>{short_id}</code>) <b>истекла</b>."

                    await bot.send_message(item["user_tg_id"], text, reply_markup=kb, parse_mode="HTML")

                    # Обновляем флаг в НОВОЙ сессии
                    async with async_session_maker() as upd_session:
                        await upd_session.execute(
                            Config.__table__.update()
                            .where(Config.id == item["config_id"])
                            .values(notify_expiry_sent=True)
                        )
                        await upd_session.commit()

                    await asyncio.sleep(0.1)

                except TelegramAPIError as e:
                    logger.warning(f"Не удалось отправить уведомление {item['user_tg_id']}: {e}")

            logger.info(f"✅ Проверка подписок завершена. Обработано: {len(notifications_data)}")

        except Exception as e:
            logger.exception(f"💥 Ошибка в send_subscription_notifications: {e}")

        jitter = random.randint(-JITTER_RANGE, JITTER_RANGE)
        await asyncio.sleep(max(900, BASE_INTERVAL + jitter))

async def send_traffic_notifications(bot: Bot):
    """
    Отправляет уведомления о трафике на основе данных из БД.
    Актуализация трафика происходит в отдельной задаче (traffic_updater).
    """
    while True:
        try:
            logger.info("🔍 Проверка уведомлений о трафике (из БД)...")
            async with async_session_maker() as session:
                # Получаем только активные конфиги — без JOIN, т.к. сервер нужен только для отображения
                configs = (await session.execute(
                    select(Config).where(Config.active == True)
                )).scalars().all()

            notified_count = 0
            for config in configs:
                try:
                    # === 1. Читаем данные из БД (уже актуальные благодаря traffic_updater) ===
                    try:
                        traffic_used = int(config.traffic_used_bytes or "0")
                        traffic_limit_gb = int(config.traffic_limit_gb or "0")
                    except (ValueError, TypeError):
                        continue

                    if traffic_limit_gb <= 0:
                        continue

                    traffic_limit_bytes = traffic_limit_gb * (1024 ** 3)
                    if traffic_limit_bytes == 0:
                        continue

                    usage_percent = (traffic_used / traffic_limit_bytes) * 100

                    # Пропускаем, если уведомления отключены или уже отправлены
                    user = await _get_notifying_user(config.user_tg_id, notify_traffic=True)
                    if not user:
                        continue

                    # === 2. Получаем имя сервера для отображения ===
                    server_name = "—"
                    if config.server_id:
                        server_result = await session.execute(
                            select(Server).where(Server.id == config.server_id)
                        )
                        server = server_result.scalar_one_or_none()
                        if server:
                            server_name = f"{server.country} ({server.city})"

                    short_id = config.id[:7] + "..." if len(config.id) > 7 else config.id
                    used_gb = traffic_used / (1024 ** 3)

                    should_notify_80 = usage_percent >= 80 and not config.notify_traffic_80_sent
                    should_notify_95 = usage_percent >= 95 and not config.notify_traffic_95_sent

                    if should_notify_95:
                        message = (
                            f"🚨 Трафик на <b>{server_name}</b> (<code>{short_id}</code>) почти <b>исчерпан</b>!\n"
                            f"Использовано: <b>{used_gb:.1f} ГБ</b> из <b>{traffic_limit_gb} ГБ</b>."
                        )
                        await _send_traffic_notification(bot, config, message, "95")
                        notified_count += 1
                    elif should_notify_80:
                        message = (
                            f"⚠️ Трафик на <b>{server_name}</b> (<code>{short_id}</code>) заканчивается!\n"
                            f"Использовано: <b>{used_gb:.1f} ГБ</b> из <b>{traffic_limit_gb} ГБ</b>."
                        )
                        await _send_traffic_notification(bot, config, message, "80")
                        notified_count += 1

                except Exception as e:
                    logger.warning(f"⚠️ Ошибка при проверке конфига {config.id}: {e}")

                # Небольшая пауза, чтобы не спамить Telegram API
                await asyncio.sleep(0.1)

            logger.info(f"✅ Проверка трафика завершена. Отправлено уведомлений: {notified_count}")

        except Exception as e:
            logger.exception(f"💥 Критическая ошибка в send_traffic_notifications: {e}")

        # Интервал: 3 часа ± джиттер
        jitter = random.randint(-JITTER_RANGE, JITTER_RANGE)
        await asyncio.sleep(max(900, BASE_INTERVAL + jitter))


async def _send_traffic_notification(bot: Bot, config, message: str, level: str):
    """Отправляет уведомление и обновляет флаги."""
    try:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Сбросить трафик", callback_data=f"renew_menu_{config.id}"),
                InlineKeyboardButton(text="✅ Понятно", callback_data=f"notification_ok_{config.id}")
            ]
        ])
        await bot.send_message(config.user_tg_id, message, reply_markup=kb, parse_mode="HTML")

        # Обновляем флаги
        async with async_session_maker() as session:
            db_config = await session.get(Config, config.id)
            if db_config:
                if level == "95":
                    db_config.notify_traffic_95_sent = True
                    db_config.notify_traffic_80_sent = True
                else:
                    db_config.notify_traffic_80_sent = True
                await session.commit()

        await asyncio.sleep(0.1)
    except TelegramAPIError as e:
        logger.warning(f"Не удалось отправить уведомление {config.user_tg_id}: {e}")


async def _get_notifying_user(tg_id: str, *, notify_expiry: bool = False, notify_traffic: bool = False) -> bool:
    async with async_session_maker() as session:
        query = select(User).where(User.tg_id == tg_id)
        if notify_expiry:
            query = query.where(User.notify_expiry == True)
        if notify_traffic:
            query = query.where(User.notify_traffic == True)
        result = await session.execute(query)
        return result.scalar_one_or_none() is not None
