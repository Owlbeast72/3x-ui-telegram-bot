from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional
import json
import logging

from config import STABLE_BASE_PRICES, MOBILE_BASE_PRICES
from services.xui_manager import XUIManager
from storage.database import async_session_maker, Config, Server, User
from utils.helpers import generate_random_prefix, gb_to_bytes


async def get_next_config_number(user_id: str) -> int:
    """Возвращает номер следующего конфига для пользователя."""
    try:
        async with async_session_maker() as session:
            result = await session.execute(
                Config.__table__.select().where(Config.user_tg_id == user_id)
            )
            configs = result.fetchall()
            return len(configs) + 1
    except Exception as e:
        logging.error(f"Ошибка в get_next_config_number: {e}")
        return 1


async def create_new_subscription(
    user_id: int,
    server_id: str,
    category: str,
    duration_days: int,  # ← Теперь это int, а не str
    username: str | None = None
) -> Optional[Dict[str, Any]]:
    """
    Создает новую подписку для пользователя.
    
    Args:
        user_id: Telegram ID пользователя
        server_id: ID сервера
        category: Категория ("mobile" или "stable")
        duration_days: Длительность в днях (целое число)
        username: Имя пользователя в Telegram (опционально)
    
    Returns:
        Словарь с данными подписки или None в случае ошибки
    """
    try:
        # Валидация
        if not isinstance(duration_days, int) or duration_days <= 0:
            logging.error(f"Некорректная длительность: {duration_days}")
            return None

        async with async_session_maker() as session:
            # Получаем сервер
            server_result = await session.execute(
                Server.__table__.select().where(Server.id == server_id)
            )
            server_row = server_result.fetchone()
            if not server_row:
                logging.error(f"Сервер не найден: {server_id}")
                return None

            # Получаем или создаём пользователя
            user_result = await session.execute(
                User.__table__.select().where(User.tg_id == str(user_id))
            )
            user_row = user_result.fetchone()
            if not user_row:
                await session.execute(
                    User.__table__.insert().values(
                        tg_id=str(user_id),
                        username=username or "",
                        first_name=username or "Anonymous"
                    )
                )
                await session.commit()

            # Генерация данных
            config_number = await get_next_config_number(str(user_id))
            client_sub_id = generate_random_prefix(16)
            random_prefix = client_sub_id
            random_prefix_email = generate_random_prefix(6)
            email = f"{random_prefix_email}_{user_id}_{config_number}"
            telegram_name = f"@{username}" if username else f"ID:{user_id}"
            comment = f"Telegram: {telegram_name} | Config #{config_number}"

            xui = XUIManager(
                base_url=server_row.xui_url,
                username=server_row.xui_username,
                password=server_row.xui_password,
                server_id=server_id
            )

            try:
                client_uuid = await xui.add_client(
                    inbound_id=server_row.inbound_id,
                    email=email,
                    user_id=user_id,
                    comment=comment,
                    expiry_days=duration_days,  # ← передаём число дней
                    client_sub_id=client_sub_id,
                    traffic_gb=100
                )

                if not client_uuid:
                    logging.error("Не удалось создать клиента в XUI")
                    return None

                inbound_data = await xui.get_inbound(server_row.inbound_id)
                if not inbound_data:
                    logging.error("Не удалось получить данные inbound")
                    return None

                server_ip = server_row.xui_url.split("//")[1].split(":")[0]

                from utils.link_builder import build_vless_reality_link
                vless_link = build_vless_reality_link(
                    client_uuid=client_uuid,
                    server_ip=server_ip,
                    inbound=inbound_data,
                    user_id=user_id,
                    config_number=config_number,
                    random_prefix=random_prefix,
                    random_prefix_email=random_prefix_email
                )

                subscription_path = server_row.subscription_path or f"/sub{client_sub_id}"
                subscription_port = server_row.subscription_port or "2096"
                subscription_link = f"http://{server_ip}:{subscription_port}{subscription_path}/{client_sub_id}"

                # Сохраняем конфиг в БД
                new_expiry = datetime.now(timezone.utc) + timedelta(days=duration_days)
                await session.execute(
                    Config.__table__.insert().values(
                        id=client_uuid,
                        user_tg_id=str(user_id),
                        server_id=server_id,
                        client_email=email,
                        base_tariff=str(duration_days),  # ← сохраняем как строку дней
                        traffic_limit_gb="100",
                        traffic_used_bytes="0",
                        expiry=new_expiry.isoformat(),
                        created_at=datetime.now(timezone.utc).isoformat(),
                        vless_link=vless_link,
                        subscription_link=subscription_link,
                        client_sub_id=client_sub_id,
                        active=True,
                        addons='{"extra_traffic_gb": 0, "traffic_reset_count": 0}'
                    )
                )
                await session.commit()

                return {
                    "vless_link": vless_link,
                    "subscription_link": subscription_link,
                    "config_id": client_uuid
                }
                
            finally:
                await xui.close()
                
    except Exception as e:
        logging.error(f"Критическая ошибка в create_new_subscription: {e}")
        return None


async def renew_subscription(
    user_id: int,
    config_id: str,
    duration_days: int
) -> bool:
    """
    Продлевает существующую подписку.
    """
    try:
        if not isinstance(duration_days, int) or duration_days <= 0:
            logging.error(f"Некорректная длительность для продления: {duration_days}")
            return False

        async with async_session_maker() as session:
            config_result = await session.execute(
                Config.__table__.select().where(
                    Config.id == config_id,
                    Config.user_tg_id == str(user_id)
                )
            )
            config_row = config_result.fetchone()
            if not config_row:
                logging.error(f"Конфиг не найден или не принадлежит пользователю: {config_id}")
                return False

            # === НОВАЯ ПРОВЕРКА: ЗАПРЕТ ПРОДЛЕНИЯ TRIAL НА УРОВНЕ СЕРВИСА ===
            if config_row.base_tariff == "Trial":
                logging.warning(f"Попытка продлить пробный конфиг {config_id}")
                return False
            # === КОНЕЦ НОВОЙ ПРОВЕРКИ ===


            server_id = config_row.server_id
            server_result = await session.execute(
                Server.__table__.select().where(Server.id == server_id)
            )
            server_row = server_result.fetchone()
            if not server_row:
                logging.error(f"Сервер не найден при продлении: {server_id}")
                return False

            xui = XUIManager(
                base_url=server_row.xui_url,
                username=server_row.xui_username,
                password=server_row.xui_password,
                server_id=server_id
            )

            try:
                success = await xui.extend_client_expiry(
                    inbound_id=server_row.inbound_id,
                    email=config_row.client_email,
                    extra_days=duration_days  # ← передаём число дней
                )
                if not success:
                    logging.error("Не удалось продлить подписку в XUI")
                    return False

                old_expiry = datetime.fromisoformat(config_row.expiry.replace("Z", "+00:00"))
                new_expiry = old_expiry + timedelta(days=duration_days)

                # === Подготавливаем данные для обновления ===
                update_values = {
                    "expiry": new_expiry.isoformat(),
                    "base_tariff": str(duration_days),
                    "notify_expiry_sent": False  # ← Сбрасываем флаг уведомления!
                }

                # === Инициализируем last_traffic_reset для тарифов > 30 дней ===
                if duration_days > 30:
                    # Проверяем, установлено ли уже значение last_traffic_reset
                    if config_row.last_traffic_reset is None:
                        update_values["last_traffic_reset"] = datetime.now(timezone.utc).isoformat()

                # Обновляем конфиг в БД
                await session.execute(
                    Config.__table__.update()
                    .where(Config.id == config_id)
                    .values(update_values)
                )
                await session.commit()
                return True
                
            finally:
                await xui.close()
                
    except Exception as e:
        logging.error(f"Критическая ошибка в renew_subscription: {e}")
        return False
