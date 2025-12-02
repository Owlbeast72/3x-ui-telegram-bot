# handlers/admin.py
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from datetime import datetime, timezone, timedelta
from uuid import uuid4
import json
from config import ADMIN_TELEGRAM_ID
from storage.database import async_session_maker, User, Config, Server, PendingPayment, get_or_create_user
from services.xui_manager import XUIManager
from utils.helpers import generate_random_prefix, gb_to_bytes, get_next_config_number
from utils.link_builder import build_vless_reality_link
from handlers.payment import process_skip_payment
router = Router()


def admin_only():
    return lambda message: message.from_user.id == ADMIN_TELEGRAM_ID


@router.callback_query(F.data.startswith("skip_payment_"))
async def skip_payment(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_TELEGRAM_ID:
        await callback.answer("❌ Запрещено.", show_alert=True)
        return
    
    invoice_id = callback.data.split("_", 2)[-1]
    await process_skip_payment(callback, state, invoice_id)
