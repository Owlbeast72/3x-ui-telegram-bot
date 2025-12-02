# handlers/renew.py
import json
from datetime import datetime, timezone
from uuid import uuid4
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode

from config import ADMIN_TELEGRAM_ID
from services.crypto_pay import create_crypto_invoice
from services.traffic_service import apply_traffic_change
from storage.database import async_session_maker, Config, Server, User, Tariff, PendingPayment
from utils.helpers import format_tariff_name

router = Router()

@router.callback_query(F.data.startswith("reset_traffic_"))
async def reset_traffic_start(callback: CallbackQuery):
    config_id = callback.data.split("_", 2)[-1]
    user_id = str(callback.from_user.id)

    # === ШАГ 1: Получаем данные из БД ===
    async with async_session_maker() as session:
        config_result = await session.execute(
            Config.__table__.select().where(
                Config.id == config_id,
                Config.user_tg_id == user_id
            )
        )
        config = config_result.fetchone()
        if not config:
            await callback.answer("Конфиг не найден.", show_alert=True)
            return

        server_result = await session.execute(
            Server.__table__.select().where(Server.id == config.server_id)
        )
        server = server_result.fetchone()
        if not server:
            await callback.message.edit_text("❌ Сервер не найден.")
            return

        user_result = await session.execute(
            User.__table__.select().where(User.tg_id == user_id)
        )
        user_row = user_result.fetchone()

    # === ШАГ 2: Работаем с X-UI ВНЕ сессии ===
    used_gb = 0
    try:
        from services.xui_manager import XUIManager
        xui = XUIManager(
            base_url=server.xui_url,
            username=server.xui_username,
            password=server.xui_password,
            server_id=server.id
        )
        used_bytes = await xui.get_client_traffic(config.client_email)
        used_gb = used_bytes / (1024 ** 3)
    finally:
        await xui.close()

    # === ШАГ 3: Рассчитываем цену и создаём счёт ===
    current_limit_gb = int(config.traffic_limit_gb)
    base_cost = max(10, round((used_gb / 100) * 140))
    final_price = base_cost

    if user_row and user_row.pending_discount_type and user_row.pending_discount_value is not None:
        disc_type = user_row.pending_discount_type
        disc_value = user_row.pending_discount_value
        if disc_type == "percent":
            discount = int(base_cost * disc_value / 100)
            final_price = max(1, base_cost - discount)
        elif disc_type == "fixed_rub":
            final_price = max(1, base_cost - disc_value)

    # === ШАГ 4: Сохраняем платёж в БД ===
    invoice_id = str(uuid4())
    payload = f"reset_traffic|{config_id}|{user_id}"
    invoice = await create_crypto_invoice(
        amount_fiat=float(final_price),
        fiat_currency="RUB",
        description="Сброс трафика",
        payload=payload
    )
    pay_url = invoice["bot_invoice_url"]
    
    async with async_session_maker() as session:
        await session.execute(
            PendingPayment.__table__.insert().values(
                payment_id=invoice_id,
                bot_invoice_id=str(invoice["invoice_id"]),
                payload=payload,
                created_at=datetime.now(timezone.utc).isoformat(),
                user_id=user_id
            )
        )
        await session.commit()

    # === ШАГ 5: Отправляем сообщение ===
    price_text = f"<b>{final_price} ₽</b>" if final_price == base_cost else f"<b>{final_price} ₽</b> (было {base_cost} ₽)"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💳 Оплатить {final_price} ₽", url=pay_url)],
        [InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"check_{invoice_id}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"manage_config_{config_id}")]
    ])
    await callback.message.edit_text(
        f"<b>♻️ Сброс трафика</b>\n\n"
        f"Использовано: <b>{used_gb:.1f} / {current_limit_gb} ГБ</b>\n"
        f"Стоимость: {price_text}\n"
        f"<i>Счёт действителен 15 минут.</i>",
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )

@router.callback_query(F.data.startswith("renew_select_duration_"))
async def renew_select_duration(callback: CallbackQuery):
    config_id = callback.data.split("_", 3)[-1]
    user_id = str(callback.from_user.id)

    async with async_session_maker() as session:
        config_result = await session.execute(
            Config.__table__.select().where(
                Config.id == config_id,
                Config.user_tg_id == user_id
            )
        )
        config = config_result.fetchone()
        if not config:
            await callback.answer("Конфиг не найден.", show_alert=True)
            return

        server_result = await session.execute(
            Server.__table__.select().where(Server.id == config.server_id)
        )
        server = server_result.fetchone()
        if not server:
            await callback.message.edit_text("❌ Сервер не найден.")
            return

    is_mobile = bool(server.mobile_spoof)

    # Получаем тарифы из БД
    tariff_result = await session.execute(
        Tariff.__table__.select()
        .where(
            Tariff.category == ("mobile" if is_mobile else "stable"),
            Tariff.active == True
        )
        .order_by(Tariff.duration_days)
    )
    available_tariffs = tariff_result.fetchall()

    buttons = []
    for tariff in available_tariffs:
        name = format_tariff_name(tariff.duration_days)
        buttons.append(
            InlineKeyboardButton(
                text=f"{name} — {tariff.price_rub} ₽",
                callback_data=f"renew_confirm_{config_id}_{tariff.id}_{tariff.price_rub}"
            )
        )
    
    keyboard = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"manage_config_{config_id}")])
    kb = InlineKeyboardMarkup(inline_keyboard=keyboard)
    await callback.message.edit_text(
        "<b>⏳ Выберите срок продления:</b>",
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data.startswith("renew_confirm_"))
async def renew_confirm(callback: CallbackQuery):
    parts = callback.data.split("_")
    config_id = parts[2]
    tariff_id = int(parts[3])
    user_id = str(callback.from_user.id)
    
    try:
        # === ВСЯ РАБОТА С БД — В ОДНОЙ СЕССИИ ===
        async with async_session_maker() as session:
            tariff = await session.get(Tariff, tariff_id)
            if not tariff:
                raise Exception("Тариф не найден")
            duration_name = format_tariff_name(tariff.duration_days)
            base_price = tariff.price_rub

            user_result = await session.execute(
                User.__table__.select().where(User.tg_id == user_id)
            )
            user_row = user_result.fetchone()

            # Применяем скидку
            final_price = base_price
            if user_row and user_row.pending_discount_type and user_row.pending_discount_value is not None:
                disc_type = user_row.pending_discount_type
                disc_value = user_row.pending_discount_value
                if disc_type == "percent":
                    discount = int(base_price * disc_value / 100)
                    final_price = max(1, base_price - discount)
                elif disc_type == "fixed_rub":
                    final_price = max(1, base_price - disc_value)

            # Создаём платёж
            payload = f"renew|{config_id}|{tariff_id}|{user_id}"
            invoice = await create_crypto_invoice(
                amount_fiat=float(final_price),
                fiat_currency="RUB",
                description=f"Продление подписки на {duration_name}",
                payload=payload
            )
            pay_url = invoice["bot_invoice_url"]
            invoice_id = str(uuid4())
            
            await session.execute(
                PendingPayment.__table__.insert().values(
                    payment_id=invoice_id,
                    bot_invoice_id=str(invoice["invoice_id"]),
                    payload=payload,
                    created_at=datetime.now(timezone.utc).isoformat(),
                    user_id=user_id
                )
            )
            await session.commit()

        # === ОТПРАВКА СООБЩЕНИЯ — ВНЕ СЕССИИ ===
        price_text = f"<b>{final_price} ₽</b>" if final_price == base_price else f"<b>{final_price} ₽</b> (было {base_price} ₽)"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"💳 Оплатить {final_price} ₽", url=pay_url)],
            [InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"check_{invoice_id}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"renew_select_duration_{config_id}")]
        ])
        await callback.message.edit_text(
            f"<b>Продление на {duration_name}</b>\n\n"
            f"Стоимость: {price_text}\n"
            f"<i>Оплата принимается в USDT (TRC20).</i>",
            reply_markup=kb,
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logging.error(f"Ошибка создания счёта на продление: {e}")
        await callback.message.edit_text(
            "❌ <b>Ошибка при создании счёта.</b>\nПопробуйте позже.",
            parse_mode=ParseMode.HTML
        )

# Блоки add_traffic и remove_traffic остаются без изменений
@router.callback_query(F.data.startswith("add_traffic_"))
async def add_traffic_start(callback: CallbackQuery):
    config_id = callback.data.split("_", 2)[-1]
    user_id = str(callback.from_user.id)
    
    # === ШАГ 1: Получаем данные из БД ===
    async with async_session_maker() as session:
        config_result = await session.execute(
            Config.__table__.select().where(
                Config.id == config_id,
                Config.user_tg_id == user_id
            )
        )
        config_row = config_result.fetchone()
        if not config_row:
            await callback.answer("Конфиг не найден.", show_alert=True)
            return

        user_result = await session.execute(
            User.__table__.select().where(User.tg_id == user_id)
        )
        user_row = user_result.fetchone()

    # === ШАГ 2: Рассчитываем цену и создаём счёт (вне сессии) ===
    BASE_TRAFFIC_PRICE = 140  # Базовая цена за +100 ГБ
    final_price = BASE_TRAFFIC_PRICE

    if user_row and user_row.pending_discount_type and user_row.pending_discount_value is not None:
        disc_type = user_row.pending_discount_type
        disc_value = user_row.pending_discount_value
        if disc_type == "percent":
            discount = int(BASE_TRAFFIC_PRICE * disc_value / 100)
            final_price = max(1, BASE_TRAFFIC_PRICE - discount)
        elif disc_type == "fixed_rub":
            final_price = max(1, BASE_TRAFFIC_PRICE - disc_value)

    invoice_id = str(uuid4())
    payload = f"add_traffic|{config_id}|{user_id}"
    
    try:
        invoice = await create_crypto_invoice(
            amount_fiat=float(final_price),
            fiat_currency="RUB",
            description="Увеличение лимита трафика на 100 ГБ",
            payload=payload
        )
        pay_url = invoice["bot_invoice_url"]
        
        # === ШАГ 3: Сохраняем платёж в БД ===
        async with async_session_maker() as session:
            await session.execute(
                PendingPayment.__table__.insert().values(
                    payment_id=invoice_id,
                    bot_invoice_id=str(invoice["invoice_id"]),
                    payload=payload,
                    created_at=datetime.now(timezone.utc).isoformat(),
                    user_id=user_id
                )
            )
            await session.commit()

        # === ШАГ 4: Отправляем сообщение ===
        price_text = f"<b>{final_price} ₽</b>" if final_price == BASE_TRAFFIC_PRICE else f"<b>{final_price} ₽</b> (было {BASE_TRAFFIC_PRICE} ₽)"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"💳 Оплатить {final_price} ₽", url=pay_url)],
            [InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"check_{invoice_id}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"manage_config_{config_id}")]
        ])
        await callback.message.edit_text(
            "<b>📈 Увеличение лимита трафика</b>\n\n"
            "Добавление <b>+100 ГБ</b> к текущему лимиту.\n"
            f"Стоимость: {price_text}\n"
            "<i>Новый лимит будет применён сразу после оплаты.</i>",
            reply_markup=kb,
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        import logging
        logging.error(f"Ошибка создания счёта на +трафик: {e}")
        await callback.message.edit_text(
            "❌ <b>Ошибка при создании счёта.</b>\nПопробуйте позже.",
            parse_mode=ParseMode.HTML
        )

@router.callback_query(F.data.startswith("remove_traffic_"))
async def remove_traffic_start(callback: CallbackQuery):
    config_id = callback.data.split("_", 2)[-1]
    user_id = str(callback.from_user.id)
    
    try:
        await apply_traffic_change(config_id, int(user_id), delta_gb=-50)
        await callback.message.edit_text(
            "✅ <b>Лимит трафика уменьшен на 50 ГБ.</b>\n"
            "Скидка 70 ₽ будет применена при следующем продлении.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад к конфигу", callback_data=f"manage_config_{config_id}")]
            ])
        )
    except ValueError as e:
        await callback.message.edit_text(
            f"❌ <b>{e}</b>",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        import logging
        logging.error(f"Ошибка уменьшения трафика: {e}")
        await callback.message.edit_text(
            "❌ <b>Не удалось изменить лимит.</b>\nПопробуйте позже.",
            parse_mode=ParseMode.HTML
        )
