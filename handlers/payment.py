# handlers/payment.py
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
import json

from config import ADMIN_TELEGRAM_ID
from services.crypto_pay import get_invoice_status
from services.subscription_service import create_new_subscription, renew_subscription
from services.traffic_service import apply_traffic_change
from storage.database import async_session_maker, PendingPayment, Config, Server, User, Tariff
from utils.helpers import gb_to_bytes

router = Router()


@router.callback_query(F.data.startswith("check_"))
async def check_payment(callback: CallbackQuery, state: FSMContext):
    try:
        invoice_id = callback.data.split("_", 1)[1]
        
        async with async_session_maker() as session:
            result = await session.execute(
                PendingPayment.__table__.select().where(PendingPayment.payment_id == invoice_id)
            )
            payment_row = result.fetchone()

            if not payment_row:
                await callback.answer("Счёт не найден или уже обработан.", show_alert=True)
                return

            payment = {
                "bot_invoice_id": payment_row.bot_invoice_id,
                "payload": payment_row.payload,
                "user_id": payment_row.user_id
            }

            invoice_status = await get_invoice_status(int(payment["bot_invoice_id"]))
            if not (invoice_status and invoice_status["status"] == "paid"):
                if callback.from_user.id == ADMIN_TELEGRAM_ID:
                    kb = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"check_{invoice_id}")],
                        [InlineKeyboardButton(text="👑 Пропустить оплату (DEBUG)", callback_data=f"skip_payment_{invoice_id}")]
                    ])
                    await callback.message.edit_text(
                        "Оплата не найдена.\n(Админ: вы можете пропустить оплату для теста)",
                        reply_markup=kb
                    )
                else:
                    await callback.answer("Оплата не найдена. Попробуйте позже.", show_alert=True)
                return

            # Обработка успешного платежа
            await _process_successful_payment(callback, state, payment["payload"], payment["user_id"], invoice_id)

    except Exception as e:
        import logging
        logging.error(f"Ошибка в check_payment: {e}")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="ОК", callback_data="start_menu")]
        ])
        await callback.message.edit_text(
            "❌ <b>Произошла ошибка при проверке оплаты.</b>\nПопробуйте позже.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb
        )


async def _process_successful_payment(callback: CallbackQuery, state: FSMContext, payload: str, user_id: str, invoice_id: str):
    """Универсальная обработка успешного платежа."""
    try:
        # 1. Сброс трафика
        if payload.startswith("reset_traffic|"):
            _, config_id, _ = payload.split("|")
            async with async_session_maker() as session:
                config_result = await session.execute(
                    Config.__table__.select().where(
                        Config.id == config_id,
                        Config.user_tg_id == str(user_id)
                    )
                )
                config_row = config_result.fetchone()
                if not config_row:
                    raise Exception("Конфиг не найден или не принадлежит вам")

                server_id = config_row.server_id
                server_result = await session.execute(
                    Server.__table__.select().where(Server.id == server_id)
                )
                server_row = server_result.fetchone()
                if not server_row:
                    raise Exception("Сервер не найден")

                from services.xui_manager import XUIManager
                xui = XUIManager(
                    base_url=server_row.xui_url,
                    username=server_row.xui_username,
                    password=server_row.xui_password,
                    server_id=server_id
                )
                try:
                    used_bytes = await xui.get_client_traffic(config_row.client_email)
                    used_gb = used_bytes / (1024 ** 3)
                    current_limit_gb = int(config_row.traffic_limit_gb)
                    success = await xui.reset_client_traffic(server_row.inbound_id, config_row.client_email)
                    if not success:
                        raise Exception("Не удалось сбросить трафик")

                    addons = json.loads(config_row.addons)
                    addons["traffic_reset_count"] = addons.get("traffic_reset_count", 0) + 1
                    await session.execute(
                        Config.__table__.update()
                        .where(Config.id == config_id)
                        .values(
                            notify_traffic_80_sent=False,
                            notify_traffic_95_sent=False
                        )
                    )
                    await session.commit()

                    kb = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="⬅️ Назад к конфигу", callback_data=f"manage_config_{config_id}")]
                    ])
                    await callback.message.edit_text(
                        f"✅ Трафик сброшен!\nИспользовано: {used_gb:.1f} / {current_limit_gb} ГБ",
                        reply_markup=kb
                    )
                finally:
                    await xui.close()

        # 2. Новая подписка
        elif len(payload.split("|")) == 6:
            _, server_id, plan_type, duration_days_str, user_id_str, final_price_str = payload.split("|")
            duration_days = int(duration_days_str)
            user_id = int(user_id_str)
            result = await create_new_subscription(
                user_id, server_id, plan_type, duration_days, callback.from_user.username
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🖼️ Сгенерировать QR-коды", callback_data=f"generate_qr_{result['config_id']}")]
            ])
            await callback.message.edit_text(
                f"""✅ Оплата подтверждена!
Ваша ссылка:
<code>{result['vless_link']}</code>
Ссылка на подписку:
<code>{result['subscription_link']}</code>""",
                parse_mode=ParseMode.HTML,
                reply_markup=kb
            )

        # 3. Продление
        elif payload.startswith("renew|"):
            _, config_id, tariff_id_str, user_id_str = payload.split("|")
            tariff_id = int(tariff_id_str)

            # Получаем длительность из тарифа по его ID
            async with async_session_maker() as session:
                tariff = await session.get(Tariff, tariff_id)
                if not tariff:
                    raise Exception(f"Тариф для продления не найден (ID: {tariff_id})")
                duration_days = tariff.duration_days

            # Продлеваем на нужное количество дней
            await renew_subscription(user_id, config_id, duration_days)
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад к конфигу", callback_data=f"manage_config_{config_id}")]
            ])
            await callback.message.edit_text("✅ Подписка успешно продлена!", reply_markup=kb)

        # 4. +100 ГБ
        elif payload.startswith("add_traffic|"):
            _, config_id, _ = payload.split("|")
            await apply_traffic_change(config_id, user_id, delta_gb=100)
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад к конфигу", callback_data=f"manage_config_{config_id}")]
            ])
            await callback.message.edit_text("✅ +100 ГБ добавлено.", reply_markup=kb)

        else:
            raise Exception("Неизвестный формат payload")

        await state.clear()
        async with async_session_maker() as session:
            await session.execute(
                User.__table__.update()
                .where(User.tg_id == user_id)
                .values(pending_discount_type=None, pending_discount_value=None)
            )
            await session.commit()

    except Exception as e:
        import logging
        logging.error(f"Ошибка в _process_successful_payment: {e}")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="ОК", callback_data="start_menu")]
        ])
        await callback.message.edit_text(
            "❌ <b>Произошла ошибка при обработке платежа.</b>\nПопробуйте позже.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb
        )


def invoice_id_from_payload(payload: str) -> str:
    """Извлекает invoice_id из payload."""
    if payload.startswith("reset_traffic|"):
        return payload.split("|")[1]
    elif len(payload.split("|")) >= 5:
        return payload.split("|")[0]
    elif payload.startswith("renew|") or payload.startswith("add_traffic|"):
        return payload.split("|")[1]
    return "unknown"


async def process_skip_payment(callback: CallbackQuery, state: FSMContext, invoice_id: str):
    """Обработка пропуска оплаты (только для админа)."""
    try:
        async with async_session_maker() as session:
            payment_row = await session.execute(
                PendingPayment.__table__.select().where(PendingPayment.payment_id == invoice_id)
            )
            payment_row = payment_row.fetchone()
            if not payment_row:
                await callback.message.edit_text("❌ Счёт не найден.")
                return
            
            # Имитируем успешную оплату
            await _process_successful_payment(
                callback, state, payment_row.payload, payment_row.user_id, invoice_id
            )
    except Exception as e:
        import logging
        logging.error(f"Ошибка в process_skip_payment: {e}")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="ОК", callback_data="start_menu")]
        ])
        await callback.message.edit_text(
            "❌ <b>Произошла ошибка при пропуске оплаты.</b>\nПопробуйте позже.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb
        )
