import aiohttp
import logging
from typing import Dict, Any

from config import CRYPTO_PAY_API_TOKEN


async def create_crypto_invoice(
    amount_fiat: float,
    fiat_currency: str,
    description: str,
    payload: str
) -> Dict[str, Any]:
    """
    Создаёт счёт в фиатной валюте, но принимает оплату в крипте (USDT).
    """
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://pay.crypt.bot/api/createInvoice",
            headers={
                "Crypto-Pay-API-Token": CRYPTO_PAY_API_TOKEN,
                "Content-Type": "application/json"
            },
            json={
                "currency_type": "fiat",
                "fiat": fiat_currency,
                "amount": str(amount_fiat),
                "accepted_assets": "USDT",
                "description": description[:1024],
                "payload": payload[:4096],
                "allow_comments": False,
                "allow_anonymous": False,
                "expires_in": 900  # 15 минут
            }
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise Exception(f"HTTP {resp.status}: {text}")
            content_type = resp.headers.get('Content-Type', '')
            if 'application/json' not in content_type:
                text = await resp.text()
                raise Exception(f"Unexpected content type: {content_type}. Response: {text}")
            data = await resp.json()
            if not data.get("ok"):
                error = data.get("error", "Unknown error")
                raise Exception(f"CryptoPay API error: {error}")
            return data["result"]


async def get_invoice_status(invoice_id: int) -> Dict[str, Any] | None:
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"https://pay.crypt.bot/api/getInvoices?invoice_ids={invoice_id}",
            headers={"Crypto-Pay-API-Token": CRYPTO_PAY_API_TOKEN}
        ) as resp:
            data = await resp.json()
            if data.get("ok") and data["result"]["items"]:
                return data["result"]["items"][0]
            return None
