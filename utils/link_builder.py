# utils/link_builder.py
import json
import urllib.parse
from typing import Dict, Any, List, Union
import logging

logger = logging.getLogger(__name__)


def _get_nested_value(data: Dict, *keys: str) -> Any:
    """
    Безопасно получает вложенное значение из словаря.
    
    Args:
        data: Словарь для поиска
        *keys: Последовательность ключей
    
    Returns:
        Найденное значение или None
    """
    for key in keys:
        if isinstance(data, dict) and key in data:
            data = data[key]
        else:
            return None
    return data


def _extract_reality_params(stream_settings: Dict[str, Any]) -> Dict[str, str]:
    """
    Извлекает параметры REALITY из streamSettings.
    
    Args:
        stream_settings: Настройки потока из 3x-ui
    
    Returns:
        Словарь параметров REALITY
    """
    reality_settings = stream_settings.get("realitySettings", {})
    
    # Публичный ключ (pbk)
    pbk = (
        _get_nested_value(reality_settings, "settings", "publicKey") or
        reality_settings.get("publicKey") or
        reality_settings.get("pbk") or
        ""
    )
    
    # SpiderX (spx)
    spx = (
        _get_nested_value(reality_settings, "settings", "spiderX") or
        reality_settings.get("spiderX") or
        ""
    )
    
    # Отпечаток (fp)
    fp = (
        _get_nested_value(reality_settings, "settings", "fingerprint") or
        reality_settings.get("fingerprint") or
        "chrome"
    )
    
    # SNI
    sni_list = reality_settings.get("serverNames") or reality_settings.get("dest") or []
    sni = sni_list[0] if isinstance(sni_list, list) and sni_list else str(sni_list) if sni_list else ""
    
    # Short ID (sid)
    sid_list = reality_settings.get("shortIds") or []
    if isinstance(sid_list, list) and sid_list:
        sid = str(sid_list[0])
    elif sid_list:
        sid = str(sid_list)
    else:
        sid = ""
    
    return {
        "pbk": str(pbk),
        "spx": str(spx),
        "fp": str(fp),
        "sni": str(sni),
        "sid": str(sid)
    }


def _extract_network_params(stream_settings: Dict[str, Any]) -> Dict[str, str]:
    """
    Извлекает параметры сети из streamSettings.
    
    Args:
        stream_settings: Настройки потока из 3x-ui
    
    Returns:
        Словарь сетевых параметров
    """
    network = stream_settings.get("network", "tcp")
    header_type = "none"
    
    if network == "tcp":
        tcp_settings = stream_settings.get("tcpSettings", {})
        header = tcp_settings.get("header", {})
        header_type = header.get("type", "none")
    elif network == "ws":
        header_type = "none"  # WebSocket не использует headerType в VLESS
    
    return {
        "network": str(network),
        "header_type": str(header_type)
    }


def build_vless_reality_link(
    client_uuid: str,
    server_ip: str,
    inbound: Dict[str, Any],
    user_id: int,
    config_number: int,
    random_prefix: str,
    random_prefix_email: str
) -> str:
    """
    Генерирует VLESS REALITY ссылку для клиента.
    
    Args:
        client_uuid: UUID клиента
        server_ip: IP-адрес сервера
        inbound: Данные inbound из 3x-ui API
        user_id: Telegram ID пользователя
        config_number: Номер конфигурации
        random_prefix: Случайный префикс для подписки
        random_prefix_email: Случайный префикс для email
    
    Returns:
        VLESS ссылка в формате строки
    """
    try:
        # Проверяем тип безопасности
        stream_settings_json = inbound.get("streamSettings", "{}")
        if isinstance(stream_settings_json, str):
            stream_settings = json.loads(stream_settings_json)
        else:
            stream_settings = stream_settings_json
        
        if stream_settings.get("security") != "reality":
            raise ValueError("Требуется REALITY security")
        
        # Извлекаем параметры
        reality_params = _extract_reality_params(stream_settings)
        network_params = _extract_network_params(stream_settings)
        port = inbound.get("port", 443)
        
        # Формируем remark
        remark = f"Reality-{random_prefix_email}_{user_id}_{config_number}"
        
        # Формируем query параметры в правильном порядке
        query_parts = [
            f"type={network_params['network']}",
            "encryption=none",
            "security=reality",
        ]
        
        if reality_params["pbk"]:
            query_parts.append(f"pbk={reality_params['pbk']}")
        
        query_parts.extend([
            f"fp={reality_params['fp']}",
            f"sni={reality_params['sni']}",
            f"sid={reality_params['sid']}",
        ])
        
        if reality_params["spx"]:
            # Кодируем spiderX безопасно
            spx_encoded = urllib.parse.quote(reality_params["spx"], safe='')
            query_parts.append(f"spx={spx_encoded}")
        
        query_parts.append("flow=xtls-rprx-vision")
        
        if network_params["header_type"] != "none":
            query_parts.append(f"headerType={network_params['header_type']}")
        
        query = "&".join(query_parts)
        return f"vless://{client_uuid}@{server_ip}:{port}?{query}#{urllib.parse.quote(remark)}"
        
    except Exception as e:
        logger.error(f"Ошибка генерации VLESS REALITY ссылки: {e}", exc_info=True)
        # Возвращаем fallback ссылку с безопасными значениями по умолчанию
        fallback_remark = f"Error-{user_id}_{config_number}"
        return (
            f"vless://{client_uuid}@{server_ip}:443?"
            f"security=reality&encryption=none&type=tcp&headerType=none&"
            f"fp=chrome&flow=xtls-rprx-vision#{urllib.parse.quote(fallback_remark)}"
        )
