# services/xui_manager.py
import os
import ssl
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from typing import Dict, Any, Optional

from aiohttp import ClientSession, TCPConnector, CookieJar, FormData

from config import SSL_CERTS_DIR
from utils.helpers import gb_to_bytes


class XUIManager:
    """Менеджер для работы с 3x-ui панелью."""

    def __init__(self, base_url: str, username: str, password: str, server_id: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.server_id = server_id
        self.session: Optional[ClientSession] = None
        self._logged_in = False

    async def ensure_login(self) -> None:
        """Гарантирует, что сессия создана и пользователь залогинен."""
        if self.session is None:
            await self._create_session()
        if not self._logged_in:
            await self.login()

    async def _create_session(self) -> None:
        """Создаёт aiohttp сессию с SSL-сертификатом."""
        cert_path = os.path.join(SSL_CERTS_DIR, f"{self.server_id}.crt")
        if not os.path.exists(cert_path):
            raise FileNotFoundError(f"SSL-сертификат не найден: {cert_path}")
        
        ssl_context = ssl.create_default_context(cafile=cert_path)
        connector = TCPConnector(ssl=ssl_context)
        cookie_jar = CookieJar(unsafe=True)
        self.session = ClientSession(connector=connector, cookie_jar=cookie_jar)

    async def login(self) -> None:
        """Выполняет вход в 3x-ui панель."""
        if self.session is None:
            raise RuntimeError("Сессия не инициализирована")

        form = FormData()
        form.add_field("username", self.username)
        form.add_field("password", self.password)
        
        async with self.session.post(f"{self.base_url}/login", data=form) as resp:
            text = await resp.text()
            if resp.status == 200 and '"success":true' in text:
                self._logged_in = True
            else:
                raise Exception(f"Ошибка входа в 3x-ui ({resp.status}): {text[:200]}...")

    async def add_client(
        self,
        inbound_id: int,
        email: str,
        user_id: int,
        comment: str,
        expiry_days: int,
        client_sub_id: str,
        traffic_gb: int = 100
    ) -> str:
        """Добавляет нового клиента в 3x-ui."""
        await self.ensure_login()
        
        expiry_timestamp = int(
            (datetime.now(timezone.utc) + timedelta(days=expiry_days)).timestamp() * 1000
        )
        client_uuid = str(uuid4())
        client = {
            "id": client_uuid,
            "email": email,
            "flow": "xtls-rprx-vision",
            "limitIp": 2,
            "totalGB": gb_to_bytes(traffic_gb),
            "expiryTime": expiry_timestamp,
            "enable": True,
            "tgId": str(user_id),
            "subId": client_sub_id,
            "comment": comment,
            "reset": 0
        }
        
        settings_str = json.dumps({"clients": [client]})
        form = FormData()
        form.add_field("id", str(inbound_id))
        form.add_field("settings", settings_str)
        
        async with self.session.post(
            f"{self.base_url}/panel/api/inbounds/addClient",
            data=form
        ) as resp:
            # Проверяем успешность операции, но НЕ возвращаем ответ
            await self._handle_json_response(resp, "addClient")
            # Возвращаем именно UUID, который мы сгенерировали
            return client_uuid

    async def extend_client_expiry(self, inbound_id: int, email: str, extra_days: int) -> bool:
        """Продлевает срок действия клиента."""
        await self.ensure_login()
        
        inbound = await self.get_inbound(inbound_id)
        settings = json.loads(inbound["settings"])
        client = next((c for c in settings["clients"] if c["email"] == email), None)
        if not client:
            raise Exception("Клиент не найден в inbound")
        
        current_expiry = client.get("expiryTime", 0)
        if current_expiry == 0:
            new_expiry = int((datetime.now(timezone.utc) + timedelta(days=extra_days)).timestamp() * 1000)
        else:
            new_expiry = current_expiry + extra_days * 86400000
        client["expiryTime"] = new_expiry
        
        return await self._update_client(inbound_id, client)

    async def backup(self) -> bytes:
        """Получает полную конфигурацию сервера в формате JSON (бэкап)."""
        await self.ensure_login()
        async with self.session.get(f"{self.base_url}/panel/api/server/getConfigJson") as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("success"):
                    # Возвращаем только obj (чистый конфиг без обёртки)
                    config_json = json.dumps(data["obj"], indent=2, ensure_ascii=False)
                    return config_json.encode("utf-8")
                else:
                    raise Exception(f"Ошибка в ответе: {data.get('msg', 'Неизвестно')}")
            else:
                text = await resp.text()
                raise Exception(f"HTTP {resp.status}: {text[:200]}")

    async def get_client_traffic(self, email: str) -> int:
        """Получает использованный трафик клиента (в байтах)."""
        await self.ensure_login()
        
        async with self.session.get(
            f"{self.base_url}/panel/api/inbounds/getClientTraffics/{email}"
        ) as resp:
            data = await self._handle_json_response(resp, "getClientTraffics")
            return data.get("obj", {}).get("down", 0)

    async def delete_client_by_email(self, inbound_id: int, email: str) -> bool:
        """Удаляет клиента по email."""
        await self.ensure_login()
        
        async with self.session.post(
            f"{self.base_url}/panel/api/inbounds/{inbound_id}/delClientByEmail/{email}"
        ) as resp:
            data = await self._handle_json_response(resp, "delClientByEmail")
            return data.get("success", False)

    async def reset_client_traffic(self, inbound_id: int, email: str) -> bool:
        """Сбрасывает использованный трафик клиента."""
        await self.ensure_login()
        
        async with self.session.post(
            f"{self.base_url}/panel/api/inbounds/{inbound_id}/resetClientTraffic/{email}"
        ) as resp:
            await self._handle_json_response(resp, "resetClientTraffic")
            return True

    async def update_client_traffic_limit(self, inbound_id: int, email: str, new_total_gb: int) -> bool:
        """Обновляет лимит трафика клиента."""
        await self.ensure_login()
        
        inbound = await self.get_inbound(inbound_id)
        settings = json.loads(inbound["settings"])
        client = next((c for c in settings["clients"] if c["email"] == email), None)
        if not client:
            raise Exception("Клиент не найден в inbound")
        
        client["totalGB"] = gb_to_bytes(new_total_gb)
        return await self._update_client(inbound_id, client)

    async def get_inbound(self, inbound_id: int) -> Dict[str, Any]:
        """Получает данные inbound."""
        await self.ensure_login()
        
        async with self.session.get(
            f"{self.base_url}/panel/api/inbounds/get/{inbound_id}"
        ) as resp:
            data = await self._handle_json_response(resp, "getInbound")
            return data["obj"]

    async def _update_client(self, inbound_id: int, client: dict) -> bool:
        """Обновляет данные клиента."""
        form = FormData()
        form.add_field("id", str(inbound_id))
        form.add_field("settings", json.dumps({"clients": [client]}))
        
        async with self.session.post(
            f"{self.base_url}/panel/api/inbounds/updateClient/{client['id']}",
            data=form
        ) as resp:
            data = await self._handle_json_response(resp, "updateClient")
            return data.get("success", False)

    async def _handle_json_response(self, resp, operation: str):
        """Унифицированная обработка JSON-ответов от 3x-ui."""
        content_type = resp.headers.get('Content-Type', '').lower()
        text = await resp.text()
        
        if 'application/json' not in content_type:
            raise Exception(
                f"3x-ui ({operation}): ожидался JSON, получен '{content_type}'. "
                f"Тело ответа: {text[:300]}..."
            )
        
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise Exception(
                f"3x-ui ({operation}): ошибка парсинга JSON: {e}. "
                f"Тело ответа: {text[:300]}..."
            )
        
        if not data.get("success"):
            msg = data.get("msg", "Неизвестная ошибка")
            raise Exception(f"3x-ui ({operation}) вернул ошибку: {msg}")
        
        return data

    async def close(self):
        """Закрывает aiohttp сессию."""
        if self.session:
            await self.session.close()
            self.session = None
            self._logged_in = False
