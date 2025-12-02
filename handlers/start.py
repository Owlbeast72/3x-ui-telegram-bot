from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode
from config import ADMIN_TELEGRAM_ID, TrialConfig
from storage.database import async_session_maker, get_or_create_user, get_user_configs
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
import json

router = Router()


def get_main_menu_keyboard(user_id: int = None) -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(text="💰 Купить", callback_data="buy_menu"),
            InlineKeyboardButton(text="📋 Мои конфиги", callback_data="my_configs")
        ],
        [
            InlineKeyboardButton(text="🔔 Напоминания", callback_data="settings"),
            InlineKeyboardButton(text="🎟️ Промокоды", callback_data="promo_menu")
        ],
        [InlineKeyboardButton(text="ℹ️ Помощь", callback_data="help_main")]
    ]
    
    if user_id == ADMIN_TELEGRAM_ID:
        keyboard.append([
            InlineKeyboardButton(text="🔐 Админка", callback_data="admin_menu")
        ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


# --- /start и возврат в главное меню ---
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await get_or_create_user(
        str(message.from_user.id),
        username=message.from_user.username or "",
        first_name=message.from_user.first_name or "Anonymous"
    )
    await message.answer(
        "🏠 <b>Главное меню</b>",
        reply_markup=get_main_menu_keyboard(message.from_user.id),
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data == "start_menu")
async def back_to_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "🏠 <b>Главное меню</b>",
        reply_markup=get_main_menu_keyboard(callback.from_user.id),
        parse_mode=ParseMode.HTML
    )


# --- Помощь: главное меню ---
@router.callback_query(F.data == "help_main")
async def help_main(callback: CallbackQuery):
    text = (
"ℹ️ <b>Помощь</b>\n\nВыберите раздел:\n\n"
"<i>*F.A.Q - Быстрые ответы на вопросы</i>\n"
)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📚 F.A.Q.", callback_data="help_faq")],
        [InlineKeyboardButton(text="📞 Поддержка", callback_data="help_support")],
        [InlineKeyboardButton(text="👤 Аккаунт", callback_data="help_account")],
        [InlineKeyboardButton(text="📌 Условия и конфиденциальность", callback_data="help_important")],
        [InlineKeyboardButton(text="◀️ В главное меню", callback_data="start_menu")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "help_important")
async def help_important(callback: CallbackQuery):
    text = (
        "📌 <b>Важная информация</b>\n\n"
        "<b>1. Предмет оказания услуги</b>\n"
        "Сервис предоставляет цифровой продукт — VLESS-конфигурацию для настройки прокси-соединения. "
        "Услуга считается оказанной в момент выдачи конфигурации в Telegram.\n\n"
        "<b>2. Возврат средств</b>\n"
        "Согласно п. 21 Постановления Правительства РФ от 27.09.2007 № 612 (в ред. №879 от 31.12.2020), "
        "цифровые товары и услуги, доступ к которым предоставлен немедленно, <b>не подлежат возврату</b>.\n\n"
        "<b>3. Обработка персональных данных</b>\n"
        "В соответствии с ФЗ‑152 «О персональных данных», обрабатываются только следующие данные:\n"
        "• Telegram ID;\n"
        "• Имя (first_name);\n"
        "• Username (при наличии).\n"
        "Данные используются исключительно для идентификации пользователя и предоставления услуги. "
        "Передача третьим лицам <b>не осуществляется</b>, за исключением случаев, прямо предусмотренных законом.\n\n"
        "<b>4. Оплата</b>\n"
        "Приобретение услуги осуществляется через платёжного агента — @CryptoBot. "
        "В рамках платежа передаются минимально необходимые технические данные (ID транзакции, сумма, описание). "
        "Обработка платежей регулируется политикой @CryptoBot.\n\n"
        "<b>5. Гарантии</b>\n"
        "Конфигурация предоставляется «как есть» (as is). "
        "Однако в случае неработоспособности конфигурации <b>по вине Исполнителя</b> (ошибка в ссылке, недоступность сервера и т.п.), "
        "Пользователь вправе обратиться в поддержку для устранения неисправности или компенсации.\n\n"
        "<b>6. Прочее</b>\n"
        "Использование сервиса означает полное согласие с настоящими условиями. "
        "Администрация оставляет за собой право вносить изменения без дополнительного уведомления. "
        "Актуальная версия всегда доступна в этом разделе."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="help_main")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)

# --- F.A.Q. ---
@router.callback_query(F.data == "help_faq")
async def help_faq(callback: CallbackQuery):
    faq_text = (
        "📚 <b>Часто задаваемые вопросы</b>\n\n"
        "Выберите вопрос или прочитайте все:\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Какие клиенты использовать?", callback_data="faq_clients")],
        [InlineKeyboardButton(text="➕ Как добавить конфигурацию?", callback_data="faq_import")],
        [InlineKeyboardButton(text="🛑 Почему не работает интернет?", callback_data="faq_not_work")],
        [InlineKeyboardButton(text="🆓 Как работает пробный период?", callback_data="faq_trial")],
        [InlineKeyboardButton(text="📊 Сбрасывается ли трафик?", callback_data="faq_traffic_reset")],
        [InlineKeyboardButton(text="🎟️ Как работают промокоды?", callback_data="faq_promo")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="help_main")]
    ])
    await callback.message.edit_text(faq_text, reply_markup=kb, parse_mode=ParseMode.HTML)


# --- FAQ: Клиенты ---
@router.callback_query(F.data == "faq_clients")
async def faq_clients(callback: CallbackQuery):
    text = (
        "📱 <b>Какие клиенты использовать?</b>\n\n"
        "✅ <b>Рекомендуемые клиенты:</b>\n\n"
        "• <b>Android</b>:\n"
        "  → <a href='https://apt.izzysoft.de/fdroid/index/apk/moe.nb4a'>Nekobox</a> (лучший выбор)\n"
        "  → Исходный код: <a href='https://github.com/MatsuriDayo/NekoBoxForAndroid'>GitHub</a>\n\n"
        "• <b>iOS</b>:\n"
        "  → <a href='https://apps.apple.com/mx/app/nekobox/id1561525911'>Nekobox для iOS</a>\n"
        "  → Альтернатива: <a href='https://apps.apple.com/app/v2raytun/id6476632852'>V2rayTun</a>\n\n"
        "• <b>Windows / Linux / macOS</b>:\n"
        "  → <a href='https://github.com/MatsuriDayo/nekoray/releases'>Nekoray (Nekobox Desktop)</a>\n\n"
        "💡 Все клиенты поддерживают импорт по ссылке <code>vless://</code> и QR-коду."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="help_faq")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


# --- FAQ: Импорт ---
@router.callback_query(F.data == "faq_import")
async def faq_import(callback: CallbackQuery):
    text = (
        "➕ <b>Как добавить конфигурацию?</b>\n\n"
        "1. Скопируйте ссылку из бота (начинается с <code>vless://</code>).\n"
        "2. Откройте клиент (например, Nekobox).\n"
        "3. Нажмите «Добавить подключение» → «Импортировать из буфера обмена».\n"
        "   — ИЛИ —\n"
        "4. Нажмите «Сканировать QR-код» и отсканируйте код из бота.\n\n"
        "✅ Готово! Подключение активно сразу после добавления."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="help_faq")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


# --- FAQ: Не работает ---
@router.callback_query(F.data == "faq_not_work")
async def faq_not_work(callback: CallbackQuery):
    text = (
        "🛑 <b>Почему не работает интернет?</b>\n\n"
        "Проверьте следующее:\n\n"
        "• 🔹 <b>Срок действия</b>: конфигурация не истекла?\n"
        "• 🔹 <b>Трафик</b>: не исчерпан лимит? (см. «Мои конфиги»)\n"
        "• 🔹 <b>Клиент</b>: обновлён ли до последней версии?\n"
        "• 🔹 <b>Сервер</b>: иногда требуется переподключиться!.\n\n"
        "Если всё в порядке — напишите в поддержку."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="help_faq")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


# --- FAQ: Пробный период ---
@router.callback_query(F.data == "faq_trial")
async def faq_trial(callback: CallbackQuery):
    text = (
        "🆓 <b>Как работает пробный период?</b>\n\n"
        "• У вас есть <b>пробные дни</b> (например, по промокоду)?\n"
        "• Нажмите «<b>Попробовать бесплатно</b>» в разделе «Купить».\n"
        "• Будет создан конфиг на первом доступном сервере.\n\n"
        "🔁 Если у вас уже есть пробный конфиг и остались свободные дни —\n"
        "нажатие той же кнопки <b>продлит</b> его на доступное количество дней.\n\n"
        "❗ Пробный период <b>нельзя продлить без остатка пробных дней</b>."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="help_faq")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


# --- FAQ: Сброс трафика ---
@router.callback_query(F.data == "faq_traffic_reset")
async def faq_traffic_reset(callback: CallbackQuery):
    text = (
        "📊 <b>Сбрасывается ли трафик?</b>\n\n"
        "Да! 🔄\n\n"
        "• Для тарифов ≤30 дней — трафик сбрасывается в ручную пользователем.\n"
        "• Для тарифов >30 дней — трафик сбрасывается <b>каждые 30 дней</b> автоматически (аналогично ежемесячному лимиту).\n\n"
        "Вы всегда видите актуальный остаток в «Мои конфиги»."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="help_faq")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


# --- FAQ: Промокоды ---
@router.callback_query(F.data == "faq_promo")
async def faq_promo(callback: CallbackQuery):
    text = (
        "🎟️ <b>Как работают промокоды?</b>\n\n"
        "• Промокод можно использовать <b>только один раз</b>.\n"
        "• Он может давать:\n"
        "  - 💰 <b>Фиксированную скидку</b>\n"
        "  - 📉 <b>Процентную скидку</b>\n"
        "  - 📅 <b>Бесплатные дни</b> (пробные дни)\n\n"
        "❗ <b>Важно:</b>\n"
        "→ Скидки <b>не суммируются</b> — новая заменяет старую.\n"
        "→ Пробные дни <b>суммируются</b> (например: +3 дня + +2 дня = 5 дней).\n\n"
        "Промокод применяется при следующей покупке или активации пробного периода."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="help_faq")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


# --- Поддержка ---
@router.callback_query(F.data == "help_support")
async def help_support(callback: CallbackQuery):
    support_text = (
        "📬 <b>Служба поддержки</b>\n\n"
        "Мы всегда рады помочь!\n\n"
        "🛠️ <b>Перед обращением:</b>\n"
        "1. Проверьте <b>F.A.Q.</b> — возможно, ваш вопрос уже решён.\n"
        "2. Убедитесь, что клиент обновлён и конфигурация активна.\n\n"
        "📩 <b>Напишите нам:</b>\n"
        "→ @nefrit_ast\n\n"
        "🕒 Ответим в течение 24 часов.\n\n"
        "💬 Пожалуйста, указывайте:\n"
        "• Ваш Telegram ID (<code>{}</code>)\n"
        "• Описание проблемы\n"
        "• Скриншот ошибки (если есть)"
    ).format(callback.from_user.id)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="help_main")]
    ])
    await callback.message.edit_text(support_text, reply_markup=kb, parse_mode=ParseMode.HTML)


# --- Аккаунт ---
@router.callback_query(F.data == "help_account")
async def help_account(callback: CallbackQuery):
    user_id = str(callback.from_user.id)
    async with async_session_maker() as session:
        result = await session.execute(select(User).where(User.tg_id == user_id))
        user = result.scalar_one_or_none()
        configs = await get_user_configs(user_id)

    if not user:
        await callback.message.edit_text("❌ Пользователь не найден.")
        return

    notify_expiry = "✅" if user.notify_expiry else "❌"
    notify_traffic = "✅" if user.notify_traffic else "❌"
    username = f"@{user.username}" if user.username else "—"

    text = (
        "👤 <b>Ваш аккаунт</b>\n\n"
        f"Имя: {user.first_name}\n"
        f"Username: {username}\n"
        f"ID: <code>{user.tg_id}</code>\n\n"
        f"Пробные дни: {user.trial_days_left} (осталось)\n"
        f"Уведомления:\n"
        f"  — об окончании: {notify_expiry}\n"
        f"  — о трафике: {notify_traffic}\n\n"
        f"Конфигураций: {len(configs)}\n"
        f"Активных: {len([c for c in configs if c.active])}"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="help_main")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


# --- Импорт модели User (чтобы не было ошибки) ---
from storage.database import User
