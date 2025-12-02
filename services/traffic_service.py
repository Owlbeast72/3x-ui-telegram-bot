# services/traffic_service.py
from typing import Dict, Any
import json

from services.xui_manager import XUIManager
from storage.database import async_session_maker, Config, Server


async def apply_traffic_change(config_id: str, user_id: int, delta_gb: int) -> None:
    session = async_session_maker()
    try:
        # Проверяем, что конфиг принадлежит пользователю
        config_result = await session.execute(
            Config.__table__.select().where(
                Config.id == config_id,
                Config.user_tg_id == str(user_id)
            )
        )
        config_row = config_result.fetchone()
        if not config_row:
            raise Exception("Конфиг не найден или не принадлежит вам")

        current_limit = int(config_row.traffic_limit_gb)
        new_limit = current_limit + delta_gb
        if new_limit < 50:
            raise ValueError("Лимит трафика не может быть ниже 50 ГБ.")

        server_id = config_row.server_id
        server_result = await session.execute(
            Server.__table__.select().where(Server.id == server_id)
        )
        server_row = server_result.fetchone()
        if not server_row:
            raise Exception("Сервер не найден")

        xui = XUIManager(
            base_url=server_row.xui_url,
            username=server_row.xui_username,
            password=server_row.xui_password,
            server_id=server_id
        )

        try:
            success = await xui.update_client_traffic_limit(
                inbound_id=server_row.inbound_id,
                email=config_row.client_email,
                new_total_gb=new_limit
            )
            if not success:
                raise Exception("Не удалось обновить лимит в панели")

            # Обновляем addons
            try:
                addons = json.loads(config_row.addons)
            except (json.JSONDecodeError, TypeError):
                addons = {"extra_traffic_gb": 0, "traffic_reset_count": 0}
            
            addons["extra_traffic_gb"] = addons.get("extra_traffic_gb", 0) + delta_gb

            # Обновляем конфиг в БД
            await session.execute(
                Config.__table__.update()
                .where(Config.id == config_id)
                .values(
                    traffic_limit_gb=str(new_limit),
                    addons=json.dumps(addons),
                    notify_traffic_80_sent=False,  # ← Сбрасываем флаги трафика!
                    notify_traffic_95_sent=False
                )
            )
            await session.commit()

        finally:
            await xui.close()

    finally:
        await session.close()  # ← Явное закрытие
