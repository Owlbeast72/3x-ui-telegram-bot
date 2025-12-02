# handlers/my_configs.py
import asyncio
from datetime import datetime, timezone
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile

from storage.database import async_session_maker, Config, Server
from utils.qr_generator import generate_qr_image
from utils.helpers import bytes_to_gb

router = Router()


def _format_config_status(config) -> str:
    """Форматирует статус конфига для отображения."""
    try:
        expiry_dt = datetime.fromisoformat(config.expiry.replace("Z", "+00:00"))
        if expiry_dt.tzinfo is None:
            expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        
        if now > expiry_dt:
            return "🔴 Истёк"
        else:
            days_left = (expiry_dt - now).days
            if days_left == 0:
                return "⚠️ Истекает сегодня"
            elif days_left == 1:
                return "⚠️ Истекает завтра"
            elif days_left <= 7:
                return f"⏳ Истекает через {days_left} дн."
            else:
                return f"🟢 Активен ({days_left} дн.)"
    except (ValueError, TypeError):
        return "❓ Статус неизвестен"


def _format_traffic_info(config) -> str:
    """Форматирует информацию о трафике."""
    try:
        used_gb = bytes_to_gb(int(config.traffic_used_bytes))
        limit_gb = int(config.traffic_limit_gb)
        percent = min(100, round((used_gb / limit_gb) * 100)) if limit_gb > 0 else 0
        
        if percent >= 95:
            traffic_emoji = "🟥"
        elif percent >= 80:
            traffic_emoji = "🟧"
        else:
            traffic_emoji = "🟩"
            
        return f"{traffic_emoji} {used_gb:.1f} / {limit_gb} ГБ ({percent}%)"
    except (ValueError, TypeError, ZeroDivisionError):
        return "❓ Трафик неизвестен"


def _get_tariff_name(config) -> str:
    """Возвращает читаемое название тарифа."""
    if config.base_tariff == "Trial":
        return "🆓 Пробный"
    else:
        try:
            days = int(config.base_tariff)
            return f"📅 {days} дн."
        except ValueError:
            return "📦 Кастомный"


def _extract_config_name(email: str) -> str:
    """Извлекает название конфига из email (часть до первого '_')."""
    return email.split("_")[0] if "_" in email else email[:6]


@router.callback_query(F.data == "my_configs")
async def my_configs(callback: CallbackQuery):
    user_id = str(callback.from_user.id)

    configs_data = []
    async with async_session_maker() as session:
        result = await session.execute(
            Config.__table__.select().where(Config.user_tg_id == user_id)
        )
        all_configs = result.fetchall()

        if not all_configs:
            text = "📭 У вас пока нет конфигураций.\n\n"
            text += "Вы можете приобрести подписку или активировать пробный период в меню «💰 Купить»."
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="start_menu")]
            ])
            await callback.message.edit_text(text, reply_markup=kb)
            return

        servers_result = await session.execute(Server.__table__.select())
        servers = {row.id: row for row in servers_result.fetchall()}

        for cfg in all_configs:
            server = servers.get(cfg.server_id)
            configs_data.append({
                "config": cfg,
                "server_country": server.country if server else "??",
                "server_city": server.city if server else "??"
            })

    def sort_key(item):
        cfg = item["config"]
        try:
            expiry_dt = datetime.fromisoformat(cfg.expiry.replace("Z", "+00:00"))
            if expiry_dt.tzinfo is None:
                expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
            is_active = datetime.now(timezone.utc) <= expiry_dt
            created_dt = datetime.fromisoformat(cfg.created_at)
            return (not is_active, -created_dt.timestamp())
        except:
            return (True, 0)

    sorted_configs = sorted(configs_data, key=sort_key)

    config_texts = []
    for i, item in enumerate(sorted_configs, 1):
        cfg = item["config"]
        tariff_name = _get_tariff_name(cfg)
        status_line = _format_config_status(cfg)
        traffic_line = _format_traffic_info(cfg)
        
        config_text = (
            f"<b>Конфиг #{i}</b>\n"
            f"Тариф: {tariff_name}\n"
            f"Сервер: {item['server_country']} ({item['server_city']})\n"
            f"Статус: {status_line}\n"
            f"Трафик: {traffic_line}\n"
            f"{'─' * 20}"
        )
        config_texts.append(config_text)

    full_text = "\n\n".join(config_texts)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"🔧 Управление ({i})",
            callback_data=f"manage_config_{item['config'].id}"
        )]
        for i, item in enumerate(sorted_configs, 1)
    ] + [[InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="start_menu")]])

    try:
        await callback.message.edit_text(
            f"📋 <b>Ваши конфигурации</b>\n\n{full_text}",
            reply_markup=kb,
            parse_mode="HTML"
        )
    except Exception:
        fallback_text = "📋 <b>Ваши конфигурации</b>\n\n"
        for i, item in enumerate(sorted_configs, 1):
            cfg = item["config"]
            status_emoji = "🟢" if "Активен" in _format_config_status(cfg) or "Истекает" in _format_config_status(cfg) else "🔴"
            fallback_text += f"{status_emoji} Конфиг #{i}\n"
        fallback_text += "\n<i>Для деталей выберите конфигурацию.</i>"
        
        await callback.message.edit_text(
            fallback_text,
            reply_markup=kb,
            parse_mode="HTML"
        )


# === НОВОЕ МЕНЮ: УПРАВЛЕНИЕ ТРАФИКОМ ===
@router.callback_query(F.data.startswith("traffic_menu_"))
async def traffic_menu(callback: CallbackQuery):
    config_id = callback.data.split("_", 2)[-1]
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

    # Получаем информацию о трафике
    try:
        used_gb = bytes_to_gb(int(config.traffic_used_bytes))
        limit_gb = int(config.traffic_limit_gb)
        percent = min(100, round((used_gb / limit_gb) * 100)) if limit_gb > 0 else 0
    except (ValueError, TypeError, ZeroDivisionError):
        used_gb, limit_gb, percent = 0, 0, 0

    # Формируем подробное сообщение
    traffic_info = (
        f"📊 <b>Управление трафиком</b>\n\n"
        f"Текущий лимит: <b>{limit_gb} ГБ</b>\n"
        f"Использовано: <b>{used_gb:.1f} ГБ</b> ({percent}%)\n\n"
        f"<i>ℹ️ Примечания:</i>\n"
        f"• <b>Сбросить трафик</b> — обнуляет счётчик использованного трафика в панели. "
        f"Более актуальное значение будет запрошено с сервера.\n"
        f"• <b>+100 ГБ</b> — мгновенно увеличивает ваш лимит на 100 ГБ.\n"
        f"• <b>–50 ГБ</b> — <b>сразу уменьшает</b> ваш лимит на 50 ГБ. "
        f"Скидка в размере 70₽ будет применена <b>только при следующем продлении</b>."
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Сбросить трафик", callback_data=f"reset_traffic_{config_id}")],
        [InlineKeyboardButton(text="📈 +100 ГБ", callback_data=f"add_traffic_{config_id}")],
        [InlineKeyboardButton(text="📉 –50 ГБ", callback_data=f"remove_traffic_{config_id}")],
        [InlineKeyboardButton(text="⬅️ Назад к управлению", callback_data=f"manage_config_{config_id}")]
    ])

    await callback.message.edit_text(traffic_info, reply_markup=kb, parse_mode="HTML")


# === ОБНОВЛЁННОЕ МЕНЮ: УПРАВЛЕНИЕ КОНФИГУРАЦИЕЙ ===
@router.callback_query(F.data.startswith("manage_config_"))
async def manage_config(callback: CallbackQuery):
    config_id = callback.data.split("_", 2)[-1]
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
            await callback.message.edit_text("❌ Сервер для этого конфига не найден.")
            return

    config_name = _extract_config_name(config.client_email)
    tariff_name = _get_tariff_name(config)
    status_line = _format_config_status(config)
    traffic_line = _format_traffic_info(config)
    
    created_dt = datetime.fromisoformat(config.created_at.replace("Z", "+00:00"))
    expiry_dt = datetime.fromisoformat(config.expiry.replace("Z", "+00:00"))
    created_str = created_dt.strftime("%d.%m.%Y %H:%M")
    expiry_str = expiry_dt.strftime("%d.%m.%Y %H:%M")
    
    server_name = f"{server.country} ({server.city})"

    details_text = (
        f"<b>📱 Детали конфигурации</b>\n\n"
        f"<b>Название:</b> <code>{config_name}</code>\n"
        f"<b>Тариф:</b> {tariff_name}\n"
        f"<b>Сервер:</b> {server_name}\n"
        f"<b>Статус:</b> {status_line}\n"
        f"<b>Трафик:</b> {traffic_line}\n\n"
        f"<b>📅 Создан:</b> {created_str}\n"
        f"<b>📆 Истекает:</b> {expiry_str}"
    )

    # Обновлённая клавиатура: "Трафик" вместо "Сбросить трафик"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📋 Ссылки", callback_data=f"copy_link_{config_id}"),
            InlineKeyboardButton(text="🖼️ QR-коды", callback_data=f"generate_qr_{config_id}")
        ],
        [
            InlineKeyboardButton(text="🔄 Продлить", callback_data=f"renew_select_duration_{config_id}"),
            InlineKeyboardButton(text="📊 Трафик", callback_data=f"traffic_menu_{config_id}")
        ],
        [
            InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"delete_config_{config_id}"),
            InlineKeyboardButton(text="⬅️ Назад", callback_data="my_configs")
        ]
    ])

    await callback.message.edit_text(details_text, reply_markup=kb, parse_mode="HTML")


# === ОСТАЛЬНЫЕ ОБРАБОТЧИКИ ===
@router.callback_query(F.data.startswith("copy_link_"))
async def copy_link(callback: CallbackQuery):
    config_id = callback.data.split("_", 2)[-1]
    user_id = str(callback.from_user.id)

    async with async_session_maker() as session:
        result = await session.execute(
            Config.__table__.select()
            .where(Config.id == config_id, Config.user_tg_id == user_id)
        )
        config = result.fetchone()

    if not config:
        await callback.answer("Конфиг не найден.", show_alert=True)
        return

    await callback.message.edit_text(
        f"📋 <b>Ваша VLESS-ссылка:</b>\n<code>{config.vless_link}</code>\n\n"
        f"🔗 <b>Ссылка на подписку:</b>\n<code>{config.subscription_link}</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад к управлению", callback_data=f"manage_config_{config_id}")]
        ])
    )


@router.callback_query(F.data.startswith("delete_config_"))
async def delete_config(callback: CallbackQuery):
    config_id = callback.data.split("_", 2)[-1]
    user_id = str(callback.from_user.id)

    async with async_session_maker() as session:
        result = await session.execute(
            Config.__table__.select()
            .where(Config.id == config_id, Config.user_tg_id == user_id)
        )
        config = result.fetchone()
        if not config:
            await callback.answer("Конфиг не найден.", show_alert=True)
            return

        server_result = await session.execute(
            Server.__table__.select().where(Server.id == config.server_id)
        )
        server = server_result.fetchone()
        if server:
            from services.xui_manager import XUIManager
            xui = XUIManager(
                base_url=server.xui_url,
                username=server.xui_username,
                password=server.xui_password,
                server_id=server.id
            )
            try:
                await xui.delete_client_by_email(int(server.inbound_id), config.client_email)
            finally:
                await xui.close()

        await session.execute(Config.__table__.delete().where(Config.id == config_id))
        await session.commit()

    await callback.message.edit_text("✅ Конфиг удалён.")
    await asyncio.sleep(1)
    await my_configs(callback)


@router.callback_query(F.data.startswith("generate_qr_"))
async def generate_qr_codes(callback: CallbackQuery):
    config_id = callback.data.split("_", 2)[-1]
    user_id = str(callback.from_user.id)

    async with async_session_maker() as session:
        result = await session.execute(
            Config.__table__.select()
            .where(Config.id == config_id, Config.user_tg_id == user_id)
        )
        config = result.fetchone()

    if not config:
        await callback.answer("Конфиг не найден.", show_alert=True)
        return

    vless_url = config.vless_link
    sub_url = config.subscription_link

    if not vless_url or not sub_url:
        await callback.message.answer("❌ Ссылки отсутствуют.")
        return

    qr_vless = generate_qr_image(vless_url)
    qr_sub = generate_qr_image(sub_url)

    await callback.message.answer_photo(
        BufferedInputFile(qr_vless.getvalue(), filename="vless_qr.png"),
        caption="📱 QR-код для VLESS-ссылки"
    )
    await callback.message.answer_photo(
        BufferedInputFile(qr_sub.getvalue(), filename="sub_qr.png"),
        caption="📋 QR-код для подписки"
    )
