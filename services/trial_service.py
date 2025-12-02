# services/trial_service.py
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
import logging

from config import TrialConfig
from storage.database import async_session_maker, User, Config, Server
from services.xui_manager import XUIManager
from utils.helpers import generate_random_prefix, get_next_config_number
from utils.link_builder import build_vless_reality_link


async def get_trial_days_left(user_id: str) -> int:
    """Возвращает оставшиеся дни Trial."""
    try:
        async with async_session_maker() as session:
            result = await session.execute(
                User.__table__.select().where(User.tg_id == user_id)
            )
            user = result.fetchone()
            return user.trial_days_left if user else 0
    except Exception as e:
        logging.error(f"Ошибка в get_trial_days_left: {e}")
        return 0


async def is_trial_available(user_id: str) -> bool:
    """Проверяет, доступен ли Trial (осталось ли дней > 0)."""
    if not TrialConfig.ENABLED:
        return False
    return await get_trial_days_left(user_id) > 0


async def add_trial_days(user_id: str, days: int) -> bool:
    """Добавляет дни Trial пользователю."""
    try:
        async with async_session_maker() as session:
            # Проверяем существование пользователя
            user_result = await session.execute(
                User.__table__.select().where(User.tg_id == user_id)
            )
            user = user_result.fetchone()
            if not user:
                return False
            
            await session.execute(
                User.__table__.update()
                .where(User.tg_id == user_id)
                .values(trial_days_left=User.trial_days_left + days)
            )
            await session.commit()
            return True
    except Exception as e:
        logging.error(f"Ошибка в add_trial_days: {e}")
        return False


async def activate_trial(user_id: str, username: str) -> Optional[Dict[str, Any]]:
    """
    Активирует Trial для пользователя на ВСЕ доступные дни.
    
    Returns:
        dict с ключами: config_id, vless_link, subscription_link, days
        или None в случае ошибки
    """
    try:
        async with async_session_maker() as session:
            # Получаем или создаём пользователя
            user_result = await session.execute(
                User.__table__.select().where(User.tg_id == user_id)
            )
            user = user_result.fetchone()
            if not user:
                return None

            # Получаем доступные дни Trial
            available_days = await get_trial_days_left(user_id)
            if available_days <= 0:
                return None

            # Получаем активные Trial конфигурации
            now = datetime.now(timezone.utc)
            config_result = await session.execute(
                Config.__table__.select().where(
                    Config.user_tg_id == user_id,
                    Config.base_tariff == "trial"
                ).order_by(Config.expiry.desc())
            )
            trial_configs = config_result.fetchall()
            
            active_trial = None
            for cfg in trial_configs:
                try:
                    expiry = datetime.fromisoformat(cfg.expiry.replace("Z", "+00:00"))
                    if expiry > now:
                        active_trial = cfg
                        break
                except (ValueError, TypeError):
                    continue  # Пропускаем некорректные даты
            
            days_to_use = available_days  # Используем ВСЕ доступные дни!
            
            if active_trial:
                # Продлеваем существующий активный Trial на все доступные дни
                try:
                    current_expiry = datetime.fromisoformat(active_trial.expiry.replace("Z", "+00:00"))
                    new_expiry = current_expiry + timedelta(days=days_to_use)
                    
                    await session.execute(
                        Config.__table__.update()
                        .where(Config.id == active_trial.id)
                        .values(expiry=new_expiry.isoformat())
                    )
                    await session.commit()
                    
                    # Обнуляем оставшиеся дни Trial у пользователя (использовали все)
                    await session.execute(
                        User.__table__.update()
                        .where(User.tg_id == user_id)
                        .values(trial_days_left=0)
                    )
                    await session.commit()
                    
                    return {
                        "config_id": active_trial.id,
                        "vless_link": active_trial.vless_link,
                        "subscription_link": active_trial.subscription_link,
                        "days": days_to_use
                    }
                except Exception as e:
                    logging.error(f"Ошибка при продлении Trial: {e}")
                    return None
            
            else:
                # Создаём новый Trial конфиг на все доступные дни
                server_result = await session.execute(
                    Server.__table__.select().where(Server.active == True)
                )
                servers = server_result.fetchall()
                if not servers:
                    logging.error("Нет активных серверов для Trial")
                    return None
                
                # Выбираем первый активный сервер
                server = servers[0]
                
                config_number = await get_next_config_number(user_id)
                client_sub_id = generate_random_prefix(16)
                random_prefix = client_sub_id
                random_prefix_email = generate_random_prefix(6)
                email = f"trial_{random_prefix_email}_{user_id}_{config_number}"
                telegram_name = f"@{username}" if username else f"ID:{user_id}"
                comment = f"Trial: {telegram_name} | Config #{config_number}"
                
                xui = XUIManager(
                    base_url=server.xui_url,
                    username=server.xui_username,
                    password=server.xui_password,
                    server_id=server.id
                )
                
                try:
                    client_uuid = await xui.add_client(
                        inbound_id=server.inbound_id,
                        email=email,
                        user_id=int(user_id),
                        comment=comment,
                        expiry_days=days_to_use,  # Используем ВСЕ доступные дни!
                        client_sub_id=client_sub_id,
                        traffic_gb=TrialConfig.TRAFFIC_GB
                    )
                    
                    if not client_uuid:
                        logging.error("Не удалось создать Trial клиента в XUI")
                        return None
                    
                    inbound_data = await xui.get_inbound(server.inbound_id)
                    if not inbound_data:
                        logging.error("Не удалось получить inbound данные для Trial")
                        return None
                    
                    server_ip = server.xui_url.split("//")[1].split(":")[0]
                    
                    vless_link = build_vless_reality_link(
                        client_uuid=client_uuid,
                        server_ip=server_ip,
                        inbound=inbound_data,
                        user_id=int(user_id),
                        config_number=config_number,
                        random_prefix=random_prefix,
                        random_prefix_email=random_prefix_email
                    )
                    
                    subscription_path = server.subscription_path or f"/sub{client_sub_id}"
                    subscription_port = server.subscription_port or "2096"
                    subscription_link = f"http://{server_ip}:{subscription_port}{subscription_path}/{client_sub_id}"
                    
                    new_expiry = now + timedelta(days=days_to_use)
                    await session.execute(
                        Config.__table__.insert().values(
                            id=client_uuid,
                            user_tg_id=user_id,
                            server_id=server.id,
                            client_email=email,
                            base_tariff="trial",
                            traffic_limit_gb=str(TrialConfig.TRAFFIC_GB),
                            traffic_used_bytes="0",
                            expiry=new_expiry.isoformat(),
                            created_at=now.isoformat(),
                            vless_link=vless_link,
                            subscription_link=subscription_link,
                            client_sub_id=client_sub_id,
                            active=True,
                            addons='{"extra_traffic_gb": 0, "traffic_reset_count": 0}'
                        )
                    )
                    await session.commit()
                    
                    # Обнуляем оставшиеся дни Trial у пользователя (использовали все)
                    await session.execute(
                        User.__table__.update()
                        .where(User.tg_id == user_id)
                        .values(trial_days_left=0)
                    )
                    await session.commit()
                    
                    return {
                        "config_id": client_uuid,
                        "vless_link": vless_link,
                        "subscription_link": subscription_link,
                        "days": days_to_use
                    }
                    
                finally:
                    await xui.close()
                    
    except Exception as e:
        logging.error(f"Критическая ошибка в activate_trial: {e}")
        return None
