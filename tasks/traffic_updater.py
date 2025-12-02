# tasks/traffic_updater.py
import asyncio
import logging
from sqlalchemy import select
from storage.database import async_session_maker, Config, Server
from services.xui_manager import XUIManager

logger = logging.getLogger(__name__)


async def update_all_traffic():
    """
    Обновляет трафик всех активных конфигов.
    Группирует конфиги по серверам, чтобы минимизировать количество входов в панель.
    """
    while True:
        try:
            logger.info("🔄 Запуск обновления трафика со всех серверов...")
            
            # === ШАГ 1: Получаем данные и КОПИРУЕМ их в простые структуры ===
            config_data_list = []
            async with async_session_maker() as session:
                result = await session.execute(
                    select(Config, Server)
                    .join(Server, Config.server_id == Server.id)
                    .where(Config.active == True)
                )
                for config, server in result:
                    config_data_list.append({
                        "config_id": config.id,
                        "client_email": config.client_email,
                        "server_id": server.id,
                        "server_data": {
                            # === КЛЮЧЕВОЕ ИЗМЕНЕНИЕ: Используем ТОЧНЫЕ имена аргументов XUIManager ===
                            "base_url": server.xui_url,
                            "username": server.xui_username,
                            "password": server.xui_password,
                            "server_id": server.id
                        },
                        "inbound_id": server.inbound_id
                    })

            if not config_data_list:
                logger.info("ℹ️ Нет активных конфигов для обновления.")
                await asyncio.sleep(3600)
                continue

            # === ШАГ 2: Группируем данные ВНЕ сессии ===
            servers_grouped = {}
            for item in config_data_list:
                sid = item["server_id"]
                servers_grouped.setdefault(sid, {
                    "server_data": item["server_data"],
                    "inbound_id": item["inbound_id"],
                    "configs": []
                })
                servers_grouped[sid]["configs"].append({
                    "config_id": item["config_id"],
                    "client_email": item["client_email"]
                })

            updated_count = 0
            for server_id, data in servers_grouped.items():
                server_info = data["server_data"]
                inbound_id = data["inbound_id"]
                configs = data["configs"]
                emails = [cfg["client_email"] for cfg in configs if cfg["client_email"]]

                if not emails:
                    continue

                try:
                    # === Теперь аргументы совпадают! ===
                    xui = XUIManager(**server_info)
                    await xui.ensure_login()

                    traffic_data = {}
                    for email in emails:
                        try:
                            used_bytes = await xui.get_client_traffic(email)
                            traffic_data[email] = used_bytes
                        except Exception as e:
                            logger.warning(f"⚠️ Не удалось получить трафик для {email} на {server_id}: {e}")
                            traffic_data[email] = 0

                    await xui.close()

                    # === ШАГ 3: Обновляем БД в НОВОЙ сессии ===
                    async with async_session_maker() as upd_session:
                        for cfg in configs:
                            used_bytes = traffic_data.get(cfg["client_email"], 0)
                            await upd_session.execute(
                                Config.__table__.update()
                                .where(Config.id == cfg["config_id"])
                                .values(traffic_used_bytes=str(used_bytes))
                            )
                        await upd_session.commit()

                    updated_count += len(configs)
                    logger.info(f"✅ Сервер {server_id}: обновлено {len(configs)} конфигов")

                except Exception as e:
                    logger.error(f"❌ Ошибка при обновлении сервера {server_id}: {e}")
                    if 'xui' in locals():
                        await xui.close()

                await asyncio.sleep(1)

            logger.info(f"✅ Обновление трафика завершено. Всего обновлено: {updated_count} конфигов")

        except Exception as e:
            logger.exception(f"💥 Критическая ошибка в update_all_traffic: {e}")

        await asyncio.sleep(3600)
