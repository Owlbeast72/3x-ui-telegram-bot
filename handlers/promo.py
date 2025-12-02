import asyncio
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from .start import get_main_menu_keyboard
from services.promocode_service import apply_promocode
from services.trial_service import get_trial_days_left
from storage.database import async_session_maker, User

router = Router()

class PromoStates(StatesGroup):
    waiting_for_code = State()


@router.callback_query(F.data == "promo_menu")
async def promo_menu(callback: CallbackQuery, state: FSMContext):
    user_id = str(callback.from_user.id)
    
    async with async_session_maker() as session:
        user_result = await session.execute(
            User.__table__.select().where(User.tg_id == user_id)
        )
        user = user_result.fetchone()

    discount_info = "ℹ️ Отсутствует"
    if user and user.pending_discount_type:
        disc_type = user.pending_discount_type
        disc_value = user.pending_discount_value
        if disc_type == "percent":
            discount_info = f"✅ {disc_value}%"
        elif disc_type == "fixed_rub":
            discount_info = f"✅ {disc_value} ₽"
    
    trial_days = await get_trial_days_left(user_id)
    trial_info = f"<b>{trial_days}</b>"

    text = (
        "<b>🎟️ Промокоды</b>\n\n"
        f"💳 <b>Текущая скидка:</b> {discount_info}\n"
        f"🆓 <b>Бесплатные дни:</b> {trial_info}\n\n"
        "ℹ️ <b>Как это работает:</b>\n"
        "• Промокоды на <b>скидку</b> не суммируются — новый заменяет старый.\n"
        "• Промокоды на <b>бесплатные дни</b> суммируются.\n\n"
        "👇 Введите промокод ниже, чтобы активировать его:"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏪ Отмена", callback_data="start_menu")]
    ])
    msg = await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    
    # Сохраняем ID сообщения для обновления после применения промокода
    await state.update_data(promo_menu_message_id=msg.message_id)
    await state.set_state(PromoStates.waiting_for_code)


@router.message(PromoStates.waiting_for_code)
async def handle_promo_code(message: Message, state: FSMContext):
    code = message.text.strip()
    if not code:
        await message.delete()
        # Всё равно сбрасываем состояние, чтобы выйти из режима ввода
        await state.clear()
        return

    user_id = str(message.from_user.id)
    result = await apply_promocode(user_id, code)

    # === Получаем обновлённые данные пользователя ===
    async with async_session_maker() as session:
        user_result = await session.execute(
            User.__table__.select().where(User.tg_id == user_id)
        )
        user = user_result.fetchone()

    # === Формируем обновлённый текст меню ===
    discount_info = "ℹ️ Отсутствует"
    if user and user.pending_discount_type:
        disc_type = user.pending_discount_type
        disc_value = user.pending_discount_value
        if disc_type == "percent":
            discount_info = f"✅ {disc_value}%"
        elif disc_type == "fixed_rub":
            discount_info = f"✅ {disc_value} ₽"

    trial_days = await get_trial_days_left(user_id)
    trial_info = f"<b>{trial_days}</b>"

    updated_text = (
        "<b>🎟️ Промокоды</b>\n\n"
        f"💳 <b>Текущая скидка:</b> {discount_info}\n"
        f"🆓 <b>Бесплатные дни:</b> {trial_info}\n\n"
        "ℹ️ <b>Как это работает:</b>\n"
        "• Промокоды на <b>скидку</b> не суммируются — новый заменяет старый.\n"
        "• Промокоды на <b>бесплатные дни</b> суммируются.\n\n"
        "👇 Введите промокод ниже, чтобы активировать его:"
    )

    # === Красивое уведомление ===
    notification_text = (
        f"{result['message']}\n\n"
        "<i>📩 Это сообщение автоматически исчезнет через 5 секунд...</i>"
    )

    temp_msg = await message.answer(notification_text, parse_mode=ParseMode.HTML)

    # === Обновляем исходное меню ===
    data = await state.get_data()
    menu_msg_id = data.get("promo_menu_message_id")

    if menu_msg_id:
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=menu_msg_id,
                text=updated_text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⏪ Отмена", callback_data="start_menu")]
                ]),
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass

    # Удаляем введённый промокод
    await message.delete()

    # === ГЛАВНОЕ ИЗМЕНЕНИЕ: СБРАСЫВАЕМ СОСТОЯНИЕ ЗДЕСЬ ===
    await state.clear()

    # Ждём 5 сек и удаляем уведомление
    await asyncio.sleep(5)
    try:
        await temp_msg.delete()
    except Exception:
        pass
