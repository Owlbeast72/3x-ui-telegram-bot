# handlers/settings.py
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from storage.database import async_session_maker, User

router = Router()


def get_settings_keyboard(notify_expiry: bool, notify_traffic: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=f"{'✅' if notify_expiry else '❌'} Истечение подписки",
                callback_data="toggle_notify_expiry"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"{'✅' if notify_traffic else '❌'} Заканчивается трафик",
                callback_data="toggle_notify_traffic"
            )
        ],
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="start_menu")
        ]
    ])


async def get_user_notifications(tg_id: str):
    async with async_session_maker() as session:
        result = await session.execute(
            User.__table__.select().where(User.tg_id == tg_id)
        )
        user = result.fetchone()
        if not user:
            # На всякий случай создаём (хотя должен существовать)
            from storage.database import get_or_create_user
            user = await get_or_create_user(tg_id)
        return user.notify_expiry, user.notify_traffic


@router.callback_query(F.data == "settings")
async def settings_menu(callback: CallbackQuery):
    user_id = str(callback.from_user.id)
    notify_expiry, notify_traffic = await get_user_notifications(user_id)
    await callback.message.edit_text(
        "⚙️ <b>Настройки уведомлений</b>\n"
        "Выберите, какие уведомления вы хотите получать:",
        reply_markup=get_settings_keyboard(notify_expiry, notify_traffic),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "toggle_notify_expiry")
async def toggle_notify_expiry(callback: CallbackQuery):
    user_id = str(callback.from_user.id)
    async with async_session_maker() as session:
        result = await session.execute(
            User.__table__.select().where(User.tg_id == user_id)
        )
        user = result.fetchone()
        new_value = not user.notify_expiry
        await session.execute(
            User.__table__.update()
            .where(User.tg_id == user_id)
            .values(notify_expiry=new_value)
        )
        await session.commit()

    notify_expiry, notify_traffic = await get_user_notifications(user_id)
    await callback.message.edit_reply_markup(
        reply_markup=get_settings_keyboard(notify_expiry, notify_traffic)
    )
    await callback.answer("✅ Настройка обновлена", show_alert=False)


@router.callback_query(F.data == "toggle_notify_traffic")
async def toggle_notify_traffic(callback: CallbackQuery):
    user_id = str(callback.from_user.id)
    async with async_session_maker() as session:
        result = await session.execute(
            User.__table__.select().where(User.tg_id == user_id)
        )
        user = result.fetchone()
        new_value = not user.notify_traffic
        await session.execute(
            User.__table__.update()
            .where(User.tg_id == user_id)
            .values(notify_traffic=new_value)
        )
        await session.commit()

    notify_expiry, notify_traffic = await get_user_notifications(user_id)
    await callback.message.edit_reply_markup(
        reply_markup=get_settings_keyboard(notify_expiry, notify_traffic)
    )
    await callback.answer("✅ Настройка обновлена", show_alert=False)
