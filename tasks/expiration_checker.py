# tasks/expiration_checker.py
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from dateutil.relativedelta import relativedelta

from sqlalchemy import select, update
from storage.database import async_session_maker, Config, Server, Tariff
from services.xui_manager import XUIManager


# --- Задача 1: Полное удаление старых конфигов ---
async def _delete_expired_configs():
    """Полное удаление конфигов, просроченных более чем на 3 дня."""
    now = datetime.now(timezone.utc)
    three_days_ago = now - timedelta(days=3)
    three_days_ago_str = three_days_ago.strftime("%Y-%m-%dT%H:%M:%S")

    async with async_session_maker() as session:
        stmt = select(Config).where(Config.expiry < three_days_ago_str)
        result = await session.execute(stmt)
        expired_configs = result.scalars().all()

        if not expired_configs:
            logging.info("Нет конфигураций для полного удаления (просрочены более 3 дней).")
            return

        logging.info(f"Найдено {len(expired_configs)} конфигураций для полного удаления.")
        delete_count = 0

        server_ids = {cfg.server_id for cfg in expired_configs}
        servers = {}
        for sid in server_ids:
            server_stmt = select(Server).where(Server.id == sid)
            server_result = await session.execute(server_stmt)
            server = server_result.scalar_one_or_none()
            if server:
                servers[sid] = server
            else:
                logging.warning(f"Сервер {sid} не найден. Конфиги будут пропущены.")

        for config in expired_configs:
            server = servers.get(config.server_id)
            if not server:
                continue

            xui = None
            try:
                xui = XUIManager(
                    base_url=server.xui_url,
                    username=server.xui_username,
                    password=server.xui_password,
                    server_id=server.id
                )
                success = await xui.delete_client_by_email(
                    inbound_id=server.inbound_id,
                    email=config.client_email
                )
                if success:
                    logging.info(f"Успешно удалён клиент {config.client_email} из 3x-ui на сервере {server.id}.")
                    await session.execute(Config.__table__.delete().where(Config.id == config.id))
                    delete_count += 1
                else:
                    logging.error(f"Не удалось удалить клиента {config.client_email} из 3x-ui.")
            except Exception as e:
                logging.error(f"Ошибка при удалении конфига {config.id}: {e}", exc_info=True)
            finally:
                if xui:
                    await xui.close()

        await session.commit()
        logging.info(f"Завершено полное удаление: {delete_count} конфигураций.")


async def deactivate_expired_subscriptions():
    """Фоновая задача для полного удаления старых конфигов (каждые 6 часов)."""
    while True:
        try:
            logging.info("Запуск проверки на полное удаление просроченных конфигураций...")
            await _delete_expired_configs()
        except Exception as e:
            logging.error(f"Критическая ошибка в задаче удаления: {e}", exc_info=True)
        await asyncio.sleep(6 * 3600)


# --- Задача 2: Ежемесячный сброс трафика ---
async def _reset_monthly_traffic():
    """Сбрасывает трафик для конфигов с тарифами > 30 дней, если прошёл календарный месяц."""
    now = datetime.now(timezone.utc)

    # === ШАГ 1: Получаем все активные конфиги и фильтруем в Python ===
    configs_to_check = []
    async with async_session_maker() as session:
        stmt = select(Config).where(Config.active == True)
        result = await session.execute(stmt)
        all_configs = result.scalars().all()

        for cfg in all_configs:
            try:
                # Проверяем, что тариф длинный
                if cfg.base_tariff != "Trial" and int(cfg.base_tariff) > 30:
                    # Получаем данные сервера
                    server = await session.get(Server, cfg.server_id)
                    if server:
                        configs_to_check.append({
                            "config_id": cfg.id,
                            "client_email": cfg.client_email,
                            "server_id": server.id,
                            "server_data": {
                                # === ИСПРАВЛЕНО: Правильные имена аргументов для XUIManager ===
                                "base_url": server.xui_url,
                                "username": server.xui_username,
                                "password": server.xui_password,
                                "server_id": server.id
                            },
                            "inbound_id": server.inbound_id, # inbound_id отдельно
                            "last_traffic_reset": cfg.last_traffic_reset,
                            "created_at": cfg.created_at
                        })
            except (ValueError, TypeError):
                continue # Пропускаем некорректные тарифы

    if not configs_to_check:
        logging.info("Нет активных конфигураций с тарифами > 30 дней для проверки сброса трафика.")
        return

    logging.info(f"Проверка сброса трафика для {len(configs_to_check)} конфигураций.")
    reset_count = 0

    for item in configs_to_check:
        # Определяем дату, с которой считать "месяц"
        reset_start_date_str = item["last_traffic_reset"] or item["created_at"]
        try:
            reset_start_date = datetime.fromisoformat(reset_start_date_str.replace("Z", "+00:00"))
            if reset_start_date.tzinfo is None:
                reset_start_date = reset_start_date.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            logging.error(f"Неверный формат даты для конфига {item['config_id']}: {reset_start_date_str}")
            continue

        next_reset_date = reset_start_date + relativedelta(months=1)
        if now >= next_reset_date:
            xui = None
            try:
                # === Теперь аргументы совпадают! ===
                xui = XUIManager(**item["server_data"])
                success = await xui.reset_client_traffic(
                    inbound_id=item["inbound_id"], # Используем отдельно сохранённый inbound_id
                    email=item["client_email"]
                )
                if success:
                    new_reset_date = now.strftime("%Y-%m-%dT%H:%M:%S")
                    # Обновляем в НОВОЙ сессии
                    async with async_session_maker() as upd_session:
                        await upd_session.execute(
                            update(Config)
                            .where(Config.id == item["config_id"])
                            .values(
                                traffic_used_bytes="0",
                                last_traffic_reset=new_reset_date
                            )
                        )
                        await upd_session.commit()
                    reset_count += 1
                    logging.info(f"Трафик сброшен для {item['client_email']}. Новая дата сброса: {new_reset_date}")
                else:
                    logging.error(f"Не удалось сбросить трафик для {item['client_email']} в 3x-ui.")
            except Exception as e:
                logging.error(f"Ошибка при сбросе трафика для {item['config_id']}: {e}", exc_info=True)
            finally:
                if xui:
                    await xui.close()

    logging.info(f"Завершена проверка сброса трафика. Выполнено сбросов: {reset_count}")

async def reset_monthly_traffic():
    """Фоновая задача для ежемесячного сброса трафика (раз в сутки)."""
    while True:
        try:
            logging.info("Запуск проверки ежемесячного сброса трафика...")
            await _reset_monthly_traffic()
        except Exception as e:
            logging.error(f"Критическая ошибка в задаче сброса трафика: {e}", exc_info=True)
        await asyncio.sleep(24 * 3600) # Раз в 24 часа
