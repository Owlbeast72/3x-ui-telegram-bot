# handlers/buy.py
from datetime import datetime, timezone
from uuid import uuid4
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext

from config import ADMIN_TELEGRAM_ID, TrialConfig 
from services.crypto_pay import create_crypto_invoice
from services.tariff_service import get_tariff_categories, get_tariffs_by_category
from services.trial_service import is_trial_available 
from storage.database import async_session_maker, Server, Tariff, PendingPayment, User
from utils.helpers import format_tariff_name, DAYS_TO_TARIFF_CODE

router = Router()


def get_duration_name(duration: str) -> str:
    return {
        "1w": "1 неделя",
        "1m": "1 месяц",
        "2m": "2 месяца",
        "3m": "3 месяца",
        "6m": "6 месяцев",
        "1y": "1 год"
    }.get(duration, duration)


@router.callback_query(F.data == "activate_trial_from_buy")
async def activate_trial_from_buy(callback: CallbackQuery):
    user_id = str(callback.from_user.id)
    username = callback.from_user.username or ""
    
    # === ШАГ 1: Выполняем ВСЮ бизнес-логику сначала ===
    try:
        from services.trial_service import activate_trial, get_trial_days_left
        from config import TrialConfig

        trial_days = await get_trial_days_left(user_id)
        if trial_days <= 0:
            result = None
        else:
            result = await activate_trial(user_id, username)
    except Exception as e:
        import logging
        logging.error(f"Ошибка в activate_trial_from_buy: {e}")
        result = None

    # === ШАГ 2: Отправляем сообщение ТОЛЬКО после завершения всех операций ===
    if result:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🖼️ Сгенерировать QR-коды", callback_data=f"generate_qr_{result['config_id']}")]
        ])
        await callback.message.edit_text(
            f"✅ <b>Пробный период активирован!</b>\n"
            f"Длительность: <b>{result['days']} день(дней)</b>\n"
            f"Лимит трафика: <b>{TrialConfig.TRAFFIC_GB} ГБ</b>\n\n"
            f"Ваша ссылка:\n<code>{result['vless_link']}</code>\n\n"
            f"Ссылка на подписку:\n<code>{result['subscription_link']}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb
        )
    else:
        await callback.message.edit_text("❌ Не удалось активировать пробный период.")


@router.callback_query(F.data == "buy_menu")
async def buy_menu(callback: CallbackQuery):
    try:
        categories = await get_tariff_categories()
        buttons = []
        
        row = []
        for cat in categories:
            emoji = "📱" if cat == "mobile" else "🛡️" if cat == "stable" else "💎"
            row.append(InlineKeyboardButton(text=f"{emoji} {cat.capitalize()}", callback_data=f"select_category_{cat}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        
        if row:
            buttons.append(row)

        if await is_trial_available(str(callback.from_user.id)):
            buttons.insert(0, [InlineKeyboardButton(text="🆓 Попробовать бесплатно", callback_data="activate_trial_from_buy")])
        
        buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="start_menu")])
        await callback.message.edit_text("Выберите тип подключения:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    except Exception as e:
        import logging
        logging.error(f"Ошибка в buy_menu: {e}")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="ОК", callback_data="start_menu")]
        ])
        await callback.message.edit_text(
            "❌ <b>Произошла ошибка.</b>\nПопробуйте позже.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb
        )


@router.callback_query(F.data.startswith("select_category_"))
async def select_category(callback: CallbackQuery):
    try:
        category = callback.data.split("_", 2)[-1]
        tariffs = await get_tariffs_by_category(category)
        
        buttons = []
        for tariff in tariffs:
            name = format_tariff_name(tariff.duration_days)
            buttons.append([
                InlineKeyboardButton(
                    text=f"{name} — {tariff.price_rub} ₽",
                    callback_data=f"select_duration_{category}_{tariff.id}"
                )
            ])
        buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="buy_menu")])
        await callback.message.edit_text("Выберите срок:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    except Exception as e:
        import logging
        logging.error(f"Ошибка в select_category: {e}")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="ОК", callback_data="buy_menu")]
        ])
        await callback.message.edit_text(
            "❌ <b>Произошла ошибка.</b>\nПопробуйте позже.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb
        )


@router.callback_query(F.data.startswith("select_duration_"))
async def select_duration(callback: CallbackQuery):
    try:
        parts = callback.data.split("_")
        plan_type = parts[2]
        duration = parts[3]
        
        async with async_session_maker() as session:
            result = await session.execute(
                Server.__table__.select().where(Server.active == True)
            )
            all_servers = result.fetchall()
        
        filtered = [
            s for s in all_servers
            if bool(s.mobile_spoof) == (plan_type == "mobile")
        ]
        
        if not filtered:
            await callback.message.edit_text(
                "❌ <b>Нет доступных серверов этого типа.</b>\n"
                "Попробуйте выбрать другой тип подключения.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="buy_menu")]
                ])
            )
            return

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"{s.country} ({s.city})",
                callback_data=f"confirm_server_{plan_type}_{duration}_{s.id}"
            )] for s in filtered
        ] + [[InlineKeyboardButton(text="⬅️ Назад", callback_data="buy_menu")]])
        
        await callback.message.edit_text(
            "<b>🌍 Выберите сервер:</b>\n"
            "Рекомендуем выбирать ближайший к вам географически.",
            reply_markup=kb,
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        import logging
        logging.error(f"Ошибка в select_duration: {e}")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="ОК", callback_data="buy_menu")]
        ])
        await callback.message.edit_text(
            "❌ <b>Произошла ошибка.</b>\nПопробуйте позже.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb
        )


@router.callback_query(F.data.startswith("confirm_server_"))
async def confirm_server(callback: CallbackQuery, state: FSMContext):
    try:
        parts = callback.data.split("_")
        category = parts[2]
        tariff_id = parts[3]
        server_id = "_".join(parts[4:])
        user_id = callback.from_user.id
        user_id_str = str(user_id)

        # Получаем тариф и сервер
        async with async_session_maker() as session:
            tariff_result = await session.execute(
                Tariff.__table__.select().where(Tariff.id == int(tariff_id))
            )
            tariff = tariff_result.fetchone()
            if not tariff:
                raise Exception("Тариф не найден")

            server_result = await session.execute(
                Server.__table__.select().where(Server.id == server_id)
            )
            server = server_result.fetchone()
            if not server:
                raise Exception("Сервер не найден")

            # Получаем данные пользователя (включая pending-скидку)
            user_result = await session.execute(
                User.__table__.select().where(User.tg_id == user_id_str)
            )
            user_row = user_result.fetchone()
            if not user_row:
                raise Exception("Пользователь не найден")

        final_price = tariff.price_rub
        original_price = tariff.price_rub
        promo_info = ""
        discount_text = ""

        # Применяем скидку, если она есть
        if user_row.pending_discount_type and user_row.pending_discount_value is not None:
            disc_type = user_row.pending_discount_type
            disc_value = user_row.pending_discount_value

            if disc_type == "percent":
                discount = int(original_price * disc_value / 100)
                final_price = max(1, original_price - discount)
                discount_text = f"<b>{final_price} ₽</b> (было {original_price} ₽)"
            elif disc_type == "fixed_rub":
                final_price = max(1, original_price - disc_value)
                discount_text = f"<b>{final_price} ₽</b> (было {original_price} ₽)"
        else:
            discount_text = f"<b>{final_price} ₽</b>"

        duration_days = tariff.duration_days

        # Генерируем invoice_id
        invoice_id = str(uuid4())
        payload = f"{invoice_id}|{server_id}|{category}|{duration_days}|{user_id}|{final_price}"

        invoice = await create_crypto_invoice(
            amount_fiat=final_price,
            fiat_currency="RUB",
            description=f"Подписка: {category} / {duration_days} дней{promo_info}",
            payload=payload
        )
        pay_url = invoice["bot_invoice_url"]

        # Сохраняем платёж в БД
        async with async_session_maker() as session:
            await session.execute(
                PendingPayment.__table__.insert().values(
                    payment_id=invoice_id,
                    bot_invoice_id=str(invoice["invoice_id"]),
                    payload=payload,
                    created_at=datetime.now(timezone.utc).isoformat(),
                    user_id=user_id_str
                )
            )
            await session.commit()

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"💳 Оплатить {final_price} ₽", url=pay_url)],
            [InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"check_{invoice_id}")],
            [InlineKeyboardButton(text="⬅️ Отменить", callback_data="buy_menu")]
        ])

        plan_emoji = "📱" if category == "mobile" else "🛡️"
        plan_name = "Мобильный обход" if category == "mobile" else "Стабильный"
        duration_name = format_tariff_name(duration_days)

        await callback.message.edit_text(
            f"<b>✅ Заказ сформирован!</b>\n\n"
            f"<b>Тип:</b> {plan_emoji} {plan_name}\n"
            f"<b>Срок:</b> {duration_name}\n"
            f"<b>Сервер:</b> {server.country} ({server.city})\n"
            f"<b>Сумма:</b> {discount_text}\n\n"
            f"<i>Оплата принимается в USDT (TRC20) по актуальному курсу.</i>",
            reply_markup=kb,
            parse_mode=ParseMode.HTML
        )

    except Exception as e:
        import logging
        logging.error(f"Ошибка в confirm_server: {e}")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="ОК", callback_data="buy_menu")]
        ])
        await callback.message.edit_text(
            "❌ <b>Произошла ошибка при создании счёта.</b>\n"
            "Попробуйте позже или выберите другой сервер.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb
        )
