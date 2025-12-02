# handlers/admin_panel.py
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import List
from sqlalchemy import func, select
from aiogram.exceptions import TelegramBadRequest
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile, ContentType
from aiogram.filters import Command, StateFilter
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from utils.helpers import format_tariff_name, format_duration_human
from config import ADMIN_TELEGRAM_ID
from storage.database import async_session_maker, User, Server, Config, Promocode, Tariff
from services.xui_manager import XUIManager

router = Router()
logger = logging.getLogger(__name__)

# Константы пагинации
USERS_PER_PAGE = 5
SERVERS_PER_PAGE = 5

class AdminStates(StatesGroup):
    waiting_for_broadcast = State()

def admin_only():
    return lambda message_or_callback: message_or_callback.from_user.id == ADMIN_TELEGRAM_ID


def get_admin_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🖥️ Сервера", callback_data="admin_servers"),
            InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users_list_0")
        ],
        [
            InlineKeyboardButton(text="💳 Тарифы", callback_data="admin_tariffs"),
            InlineKeyboardButton(text="🎟️ Промокоды", callback_data="admin_promocodes")
        ],
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"),
            InlineKeyboardButton(text="💾 Бэкап 3x-ui", callback_data="admin_backup")
        ],
        [
            InlineKeyboardButton(text="📢 Объявление", callback_data="admin_broadcast"),
            InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="start_menu")
        ]
    ])


class AdminTariffStates(StatesGroup):
    waiting_for_new_price = State()
    waiting_for_new_traffic = State()  # можно расширить позже


@router.callback_query(F.data.startswith("tariff_edit_price_"), admin_only())
async def edit_tariff_price_start(callback: CallbackQuery, state: FSMContext):
    tariff_id = callback.data.split("_")[-1]
    await state.update_data(editing_tariff_id=tariff_id)
    await callback.message.edit_text(
        f"<b>✏️ Введите новую цену (в рублях) для тарифа ID {tariff_id}:</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_tariffs")]
        ])
    )
    await state.set_state(AdminTariffStates.waiting_for_new_price)


@router.message(StateFilter(AdminTariffStates.waiting_for_new_price), admin_only())
async def process_new_price(message: Message, state: FSMContext):
    try:
        new_price = int(message.text.strip())
        if new_price <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите корректное положительное число.")
        return

    data = await state.get_data()
    tariff_id = data["editing_tariff_id"]

    async with async_session_maker() as session:
        tariff = await session.get(Tariff, tariff_id)
        if not tariff:
            await message.answer("❌ Тариф не найден.")
            await state.clear()
            return
        tariff.price_rub = new_price
        await session.commit()

    await message.answer("✅ Цена обновлена!")
    await state.clear()
    # Вернём в админ-меню тарифов — но нужно callback
    # Поскольку мы в message, просто отправим новое сообщение
    from services.tariff_service import get_all_tariffs
    tariffs = await get_all_tariffs()
    text = "<b>💳 Тарифы:</b>\n\n"
    for t in tariffs:
        status = "🟢" if t.active else "🔴"
        name = format_tariff_name(t.duration_days)
        text += f"{status} {name} — {t.price_rub} ₽ ({t.category})\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_menu")]
    ])
    await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)

@router.message(Command("admin"), admin_only())
async def cmd_admin(message: Message):
    await message.answer(
        "<b>🔐 Админ-панель</b>\n\nВыберите раздел для управления:",
        reply_markup=get_admin_menu_keyboard(),
        parse_mode=ParseMode.HTML
    )

@router.callback_query(F.data == "admin_broadcast", admin_only())
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "<b>📢 Рассылка объявления</b>\n\n"
        "Отправьте сообщение, которое будет разослано <b>всем пользователям</b>.\n"
        "Поддерживаются: текст, фото, видео, документы, пересылка.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Отмена", callback_data="admin_menu")]
        ])
    )
    await state.set_state(AdminStates.waiting_for_broadcast)  # ← используем State

@router.message(StateFilter(AdminStates.waiting_for_broadcast))
async def handle_broadcast_message(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    
    # Получаем всех пользователей
    async with async_session_maker() as session:
        result = await session.execute(User.__table__.select())
        users = result.fetchall()
    
    success_count = 0
    total = len(users)
    
    await message.answer(f"📤 Начинаю рассылку {total} пользователям...")
    
    for user in users:
        try:
            if message.text:
                await bot.send_message(user.tg_id, message.text, parse_mode=ParseMode.HTML)
            elif message.photo:
                photo = message.photo[-1]  # самое большое
                await bot.send_photo(
                    user.tg_id,
                    photo.file_id,
                    caption=message.caption,
                    parse_mode=ParseMode.HTML if message.caption else None
                )
            elif message.document:
                await bot.send_document(
                    user.tg_id,
                    message.document.file_id,
                    caption=message.caption,
                    parse_mode=ParseMode.HTML if message.caption else None
                )
            elif message.video:
                await bot.send_video(
                    user.tg_id,
                    message.video.file_id,
                    caption=message.caption,
                    parse_mode=ParseMode.HTML if message.caption else None
                )
            else:
                # Пересылка (если нельзя отправить как есть)
                await bot.forward_message(user.tg_id, message.chat.id, message.message_id)
            
            success_count += 1
            await asyncio.sleep(0.05)  # защита от flood
            
        except Exception as e:
            # Пользователь заблокировал бота или удалил чат — пропускаем
            continue
    
    await message.answer(f"✅ Рассылка завершена!\nУспешно: {success_count}/{total}")

@router.callback_query(F.data == "admin_menu", admin_only())
async def admin_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "<b>🔐 Админ-панель</b>\n\nВыберите раздел для управления:",
        reply_markup=get_admin_menu_keyboard(),
        parse_mode=ParseMode.HTML
    )

@router.callback_query(F.data.startswith("edit_server_"), admin_only())
async def edit_server_start(callback: CallbackQuery):
    server_id = callback.data.split("_", 2)[-1]
    async with async_session_maker() as session:
        server = await session.get(Server, server_id)
        if not server:
            await callback.message.edit_text("❌ Сервер не найден.")
            return

    status = "🟢 Активен" if server.active else "🔴 Неактивен"
    text = (
        f"<b>🖥️ Сервер: {server.id}</b>\n\n"
        f"Страна: {server.country}\n"
        f"Город: {server.city}\n"
        f"URL: {server.xui_url}\n"
        f"Статус: {status}\n\n"
        f"<i>Управление сервером:</i>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑️ Удалить сервер", callback_data=f"delete_server_{server_id}")],
        [InlineKeyboardButton(text="🔄 Переключить статус", callback_data=f"toggle_server_{server_id}")],
        [InlineKeyboardButton(text="⬅️ Назад к списку", callback_data="admin_servers")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)

@router.callback_query(F.data.startswith("toggle_server_"), admin_only())
async def toggle_server_status(callback: CallbackQuery):
    server_id = callback.data.split("_", 2)[-1]
    async with async_session_maker() as session:
        server = await session.get(Server, server_id)
        if not server:
            await callback.message.edit_text("❌ Сервер не найден.")
            return
        server.active = not server.active
        await session.commit()
    await edit_server_start(callback)  # Обновить карточку

@router.callback_query(F.data.startswith("delete_server_"), admin_only())
async def delete_server(callback: CallbackQuery, state: FSMContext):
    server_id = callback.data.split("_", 2)[-1]
    async with async_session_maker() as session:
        server = await session.get(Server, server_id)
        if not server:
            await callback.answer("Сервер уже удалён.", show_alert=True)
            return
        await session.delete(server)
        await session.commit()
    
    await callback.answer("✅ Сервер удалён", show_alert=False)
    # Правильно: вызываем с тем же state
    await admin_servers(callback, state)

# ==================== РАСШИРЕННАЯ СТАТИСТИКА ====================
@router.callback_query(F.data == "admin_stats", admin_only())
async def admin_stats(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await _update_stats_message(callback.message)


async def _update_stats_message(message):
    now_iso = datetime.now(timezone.utc).isoformat()

    async with async_session_maker() as session:
        # Пользователи
        user_result = await session.execute(select(User))
        users = user_result.scalars().all()
        
        # Все конфиги
        config_result = await session.execute(select(Config))
        all_configs = config_result.scalars().all()
        
        # Активные конфиги = active=True И expiry > сейчас
        active_configs = [
            c for c in all_configs 
            if c.active and c.expiry and c.expiry > now_iso
        ]
        
        # Уникальные пользователи с активными конфигами
        active_user_ids = {c.user_tg_id for c in active_configs}
        active_users_count = len(active_user_ids)
        
        # Сервера
        server_result = await session.execute(select(Server))
        servers = server_result.scalars().all()
        active_servers = [s for s in servers if s.active]
        
        # Трафик
        total_traffic = sum(
            int(cfg.traffic_used_bytes) 
            for cfg in all_configs 
            if cfg.traffic_used_bytes and cfg.traffic_used_bytes.isdigit()
        )
        total_gb = total_traffic / (1024 ** 3)
    
    text = (
        "<b>📊 Статистика</b>\n\n"
        f"👥 <b>Пользователей:</b> {len(users)}\n"
        f"   └─ С активной подпиской: {active_users_count}\n\n"
        f"🔌 <b>Конфигураций:</b> {len(all_configs)}\n"
        f"   └─ Активных: {len(active_configs)}\n\n"
        f"🌐 <b>Серверов:</b> {len(servers)}\n"
        f"   └─ Активных: {len(active_servers)}\n\n"
        f"📈 <b>Использовано трафика:</b> {total_gb:.2f} ГБ\n"
        f"🕒 <b>Обновлено:</b> {datetime.now().strftime('%d.%m %H:%M')}"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_stats_refresh")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_menu")]
    ])
    
    try:
        await message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            pass  # Игнорируем
        else:
            raise
@router.callback_query(F.data == "admin_stats_refresh", admin_only())
async def admin_stats_refresh(callback: CallbackQuery):
    await _update_stats_message(callback.message)


# ==================== УПРАВЛЕНИЕ СЕРВЕРАМИ ====================
@router.callback_query(F.data.startswith("admin_servers_page_"), admin_only())
@router.callback_query(F.data == "admin_servers", admin_only())
async def admin_servers(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    # Определяем страницу
    page = 0
    offset = page * SERVERS_PER_PAGE

    async with async_session_maker() as session:
        total_result = await session.execute(select(func.count()).select_from(Server))
        total_servers = total_result.scalar()

        server_result = await session.execute(
            select(Server)
            .order_by(Server.id)
            .offset(offset)
            .limit(SERVERS_PER_PAGE)
        )
        servers = server_result.scalars().all()

        config_counts = {}
        if servers:
            server_ids = [s.id for s in servers]
            counts = await session.execute(
                select(Config.server_id, func.count(Config.id))
                .where(Config.server_id.in_(server_ids))
                .group_by(Config.server_id)
            )
            config_counts = {row[0]: row[1] for row in counts.fetchall()}

    buttons = []
    for server in servers:
        status_icon = "🟢" if server.active else "🔴"
        config_count = config_counts.get(server.id, 0)
        label = f"{status_icon} {server.id} | {server.country} ({config_count} конф.)"
        buttons.append([
            InlineKeyboardButton(text=label, callback_data=f"edit_server_{server.id}")
        ])

    nav = []
    if (page + 1) * SERVERS_PER_PAGE < total_servers:
        nav.append(InlineKeyboardButton(text="След ➡️", callback_data=f"admin_servers_page_{page + 1}"))
    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton(text="➕ Добавить сервер", callback_data="admin_add_server")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="admin_menu")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text(
        f"<b>🖥️ Сервера (стр. {page + 1})</b>\n"
        "Нажмите на сервер для управления:",
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )
@router.callback_query(F.data == "admin_add_server", admin_only())
async def admin_add_server_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "<b>➕ Добавление сервера</b>\n\n"
        "Отправьте данные сервера в формате JSON:\n\n"
        "<code>{\n"
        '  "id": "de-fra-01",\n'
        '  "country": "🇩🇪Germany",\n'
        '  "city": "Frankfurt",\n'
        '  "xui_url": "https://your-server.com",\n'
        '  "xui_username": "admin",\n'
        '  "xui_password": "password",\n'
        '  "inbound_id": "1",\n'
        '  "mobile_spoof": true,\n'
        '  "subscription_path": "/sub",\n'
        '  "subscription_port": "2096",\n'
        '  "active": true\n'
        "}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Отмена", callback_data="admin_servers")]
        ])
    )


# ==================== ПОСТРАНИЧНЫЙ ВЫВОД ПОЛЬЗОВАТЕЛЕЙ ====================
@router.callback_query(F.data.startswith("admin_users_list_"), admin_only())
async def admin_users_list(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    page = int(callback.data.split("_")[-1])
    await _show_users_page(callback.message, page)


async def _show_users_page(message, page: int):
    offset = page * USERS_PER_PAGE
    
    async with async_session_maker() as session:
        # Подсчёт через ORM
        count_result = await session.execute(select(func.count()).select_from(User))
        total_users = count_result.scalar()
        
        # Запрос пользователей
        result = await session.execute(
            select(User)
            .order_by(User.created_at.desc())
            .offset(offset)
            .limit(USERS_PER_PAGE)
        )
        users = result.scalars().all()
    
    if not users:
        text = "❌ Нет пользователей в базе данных."
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_menu")]
        ])
    else:
        text = f"<b>👥 Пользователи (стр. {page + 1})</b>\n\n"
        for user in users:
            username = f"@{user.username}" if user.username else "—"
            text += f"• <b>{user.tg_id}</b> | {user.first_name} | {username}\n"
        
        # Кнопки пагинации
        buttons = []
        if page > 0:
            buttons.append(InlineKeyboardButton(text="⬅️ Пред", callback_data=f"admin_users_list_{page - 1}"))
        if (page + 1) * USERS_PER_PAGE < total_users:
            buttons.append(InlineKeyboardButton(text="След ➡️", callback_data=f"admin_users_list_{page + 1}"))
        
        keyboard = [buttons] if buttons else []
        keyboard.append([InlineKeyboardButton(text="🔍 Поиск по ID", callback_data="admin_users_search")])
        keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_menu")])
        kb = InlineKeyboardMarkup(inline_keyboard=keyboard)
    
    await message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "admin_users_search", admin_only())
async def admin_users_search(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "<b>🔍 Поиск пользователя</b>\n\nОтправьте Telegram ID:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад к списку", callback_data="admin_users_list_0")]
        ])
    )


# ==================== БЭКАП 3X-UI ====================
@router.callback_query(F.data == "admin_backup", admin_only())
async def admin_backup(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "<b>💾 Бэкап 3x-ui</b>\n\nВыберите сервер для создания резервной копии:",
        parse_mode=ParseMode.HTML,
        reply_markup=await _get_backup_servers_keyboard()
    )


async def _get_backup_servers_keyboard():
    async with async_session_maker() as session:
        result = await session.execute(Server.__table__.select().where(Server.active == True))
        servers = result.fetchall()
    
    buttons = [
        [InlineKeyboardButton(text=f"{s.country} ({s.city})", callback_data=f"backup_server_{s.id}")]
        for s in servers
    ]
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data.startswith("backup_server_"), admin_only())
async def backup_server(callback: CallbackQuery, bot: Bot):
    server_id = callback.data.split("_", 2)[-1]
    
    async with async_session_maker() as session:
        server_result = await session.execute(
            Server.__table__.select().where(Server.id == server_id)
        )
        server = server_result.fetchone()
        if not server:
            await callback.message.edit_text("❌ Сервер не найден.")
            return

    try:
        await callback.message.edit_text("⏳ Создание бэкапа конфигурации...")

        xui = XUIManager(
            base_url=server.xui_url,
            username=server.xui_username,
            password=server.xui_password,
            server_id=server_id
        )
        
        backup_data = await xui.backup()
        filename = f"xray_config_{server_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        await bot.send_document(
            chat_id=callback.from_user.id,
            document=BufferedInputFile(backup_data, filename=filename),
            caption=f"✅ Бэкап конфигурации сервера <b>{server_id}</b>",
            parse_mode=ParseMode.HTML
        )
        
    except Exception as e:
        logger.error(f"Ошибка бэкапа для {server_id}: {e}")
        await callback.message.edit_text(f"❌ Ошибка: {e}")
    finally:
        await xui.close()

@router.callback_query(F.data == "admin_tariffs", admin_only())
async def admin_tariffs(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    from services.tariff_service import get_all_tariffs
    tariffs = await get_all_tariffs()
    
    text = "<b>💳 Тарифы:</b>\n\n"
    buttons = []
    for t in tariffs:
        status = "🟢" if t.active else "🔴"
        name = format_tariff_name(t.duration_days)
        text += f"{status} {name} — {t.price_rub} ₽ ({t.category})\n"
        text += f"   ID: {t.id} | Трафик: {t.traffic_gb} ГБ\n\n"
        buttons.append([
            InlineKeyboardButton(text="✏️ Цена", callback_data=f"tariff_edit_price_{t.id}"),
            InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"tariff_delete_{t.id}")
        ])
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить тариф", callback_data="admin_add_tariff")],
        *buttons,
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_menu")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)

@router.callback_query(F.data.startswith("tariff_delete_"), admin_only())
async def delete_tariff(callback: CallbackQuery, state: FSMContext):
    tariff_id = int(callback.data.split("_")[-1])
    async with async_session_maker() as session:
        tariff = await session.get(Tariff, tariff_id)
        if not tariff:
            await callback.answer("Тариф не найден.", show_alert=True)
            return
        await session.delete(tariff)
        await session.commit()
    await callback.answer("✅ Тариф удалён", show_alert=False)
    await admin_tariffs(callback, state)

@router.callback_query(F.data == "admin_add_tariff", admin_only())
async def admin_add_tariff_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "<b>➕ Добавление тарифа</b>\n\n"
        "Отправьте данные в формате:\n"
        "<code>категория|дней|цена|трафик</code>\n\n"
        "Пример:\n<code>stable|30|400|100</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Отмена", callback_data="admin_tariffs")]
        ])
    )


PROMOS_PER_PAGE = 5

@router.callback_query(F.data.startswith("admin_promos_page_"), admin_only())
@router.callback_query(F.data == "admin_promocodes", admin_only())
async def admin_promocodes_list(callback: CallbackQuery, state: FSMContext):
    if callback.data == "admin_promocodes":
        page = 0
    else:
        try:
            page = int(callback.data.split("_")[-1])
        except (ValueError, IndexError):
            page = 0

    # Сохраняем текущую страницу
    await state.update_data(promo_page=page)

    await _render_promo_list(callback.message, page)

async def _render_promo_list(message: Message, page: int):
    offset = page * PROMOS_PER_PAGE

    async with async_session_maker() as session:
        total_result = await session.execute(select(func.count()).select_from(Promocode))
        total = total_result.scalar()
        
        result = await session.execute(
            select(Promocode)
            .order_by(Promocode.id.desc())
            .offset(offset)
            .limit(PROMOS_PER_PAGE)
        )
        promos = result.scalars().all()
    
    text = "<b>🎟️ Промокоды:</b>\n\n"
    buttons = []
    for p in promos:
        status = "🟢" if p.active else "🔴"
        type_ru = {
            "fixed_days": "Дни Trial",
            "percent": "Скидка %",
            "fixed_rub": "Скидка ₽"
        }.get(p.discount_type, p.discount_type)
        
        text += f"{status} <code>{p.code}</code>\n"
        text += f"   Тип: {type_ru} | Значение: {p.discount_value}\n"
        text += f"   Использовано: {p.used_count}/{p.max_uses}\n\n"
        
        buttons.append([
            InlineKeyboardButton(text="🔧Редактировать", callback_data=f"promo_detail_{p.code_hash}"),
            InlineKeyboardButton(text="🗑️Удалить", callback_data=f"promo_del_{p.code_hash}")
        ])
    
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Пред", callback_data=f"admin_promos_page_{page - 1}"))
    if (page + 1) * PROMOS_PER_PAGE < total:
        nav.append(InlineKeyboardButton(text="След ➡️", callback_data=f"admin_promos_page_{page + 1}"))
    if nav:
        buttons.append(nav)
    
    buttons.append([InlineKeyboardButton(text="➕ Создать", callback_data="admin_create_promo")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_menu")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("promo_detail_"), admin_only())
async def promo_detail(callback: CallbackQuery):
    promo_code_hash = callback.data.split("_", 2)[-1]
    
    async with async_session_maker() as session:
        result = await session.execute(
            select(Promocode).where(Promocode.code_hash == promo_code_hash)
        )
        promo = result.scalar_one_or_none()
        if not promo:
            await callback.answer("Промокод не найден.", show_alert=True)
            return
        
        type_ru = {
            "fixed_days": "Дни Trial",
            "percent": "Скидка %",
            "fixed_rub": "Скидка ₽"
        }.get(promo.discount_type, promo.discount_type)
        
        text = (
            f"<b>🎟️ Промокод: <code>{promo.code}</code></b>\n\n"
            f"Тип: {type_ru}\n"
            f"Значение: {promo.discount_value}\n"
            f"Макс. использований: {promo.max_uses}\n"
            f"Использовано: {promo.used_count}\n"
            f"Активен: {'✅ Да' if promo.active else '❌ Нет'}"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="🔄 Выключить" if promo.active else "🔄 Включить",
                callback_data=f"promo_toggle_{promo_code_hash}"
            )],
            [InlineKeyboardButton(
                text="🗑️ Удалить",
                callback_data=f"promo_del_{promo_code_hash}"
            )],
            [InlineKeyboardButton(text="⬅️ Назад к списку", callback_data="admin_promocodes")]
        ])
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)

@router.callback_query(F.data == "admin_create_promo", admin_only())
async def admin_create_promo_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "<b>➕ Создание промокода</b>\n\n"
        "Отправьте данные в формате:\n"
        "<code>код|тип|значение|макс_использований</code>\n\n"
        "Типы: <code>fixed_days</code>, <code>percent</code>, <code>fixed_rub</code>\n"
        "Пример:\n<code>WELCOME|fixed_days|3|100</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Отмена", callback_data="admin_promocodes")]
        ])
    )

# Продление (для админа)
@router.callback_query(F.data.startswith("admin_renew_"), admin_only())
async def admin_renew_config(callback: CallbackQuery):
    config_id = callback.data.split("_", 2)[-1]
    
    async with async_session_maker() as session:
        config = await session.get(Config, config_id)
        if not config:
            await callback.message.edit_text("❌ Конфиг не найден.")
            return
        real_user_id = config.user_tg_id  # ← правильный ID владельца

    from services.subscription_service import renew_subscription
    success = await renew_subscription(real_user_id, config_id, 30)

    if success:
        await callback.message.edit_text("✅ Подписка продлена на 30 дней!")
    else:
        await callback.message.edit_text("❌ Не удалось продлить подписку.")

# Сброс трафика (для админа, без оплаты)
@router.callback_query(F.data.startswith("admin_reset_traffic_"), admin_only())
async def admin_reset_traffic(callback: CallbackQuery):
    config_id = callback.data.split("_", 3)[-1]
    
    try:
        async with async_session_maker() as session:
            # 1. Получаем конфиг
            config_result = await session.execute(
                Config.__table__.select().where(Config.id == config_id)
            )
            config_row = config_result.fetchone()
            if not config_row:
                await callback.message.edit_text("❌ Конфиг не найден.")
                return

            # 2. Получаем сервер
            server_id = config_row.server_id
            server_result = await session.execute(
                Server.__table__.select().where(Server.id == server_id)
            )
            server_row = server_result.fetchone()
            if not server_row:
                await callback.message.edit_text("❌ Сервер не найден.")
                return

            # 3. Сбрасываем трафик через XUIManager
            xui = XUIManager(
                base_url=server_row.xui_url,
                username=server_row.xui_username,
                password=server_row.xui_password,
                server_id=server_id
            )
            try:
                # Выполняем настоящий сброс на сервере
                success = await xui.reset_client_traffic(server_row.inbound_id, config_row.client_email)
                if not success:
                    raise Exception("Не удалось сбросить трафик в панели 3x-ui")

                # Обновляем флаги в БД (чтобы уведомления приходили снова)
                await session.execute(
                    Config.__table__.update()
                    .where(Config.id == config_id)
                    .values(
                        notify_traffic_80_sent=False,
                        notify_traffic_95_sent=False
                    )
                )
                await session.commit()

                await callback.message.edit_text("✅ Трафик успешно сброшен!")

            finally:
                await xui.close()

    except Exception as e:
        logger.error(f"Ошибка сброса трафика для админа: {e}")
        await callback.message.edit_text(f"❌ Ошибка: {str(e)}")

# Удаление конфига
@router.callback_query(F.data.startswith("admin_delete_config_"), admin_only())
async def admin_delete_config(callback: CallbackQuery):
    config_id = callback.data.split("_", 3)[-1]
    async with async_session_maker() as session:
        # Получаем конфиг, чтобы удалить из X-UI
        config_result = await session.execute(
            Config.__table__.select().where(Config.id == config_id)
        )
        config = config_result.fetchone()
        if not config:
            await callback.message.edit_text("❌ Конфиг не найден.")
            return

        # Удаляем из X-UI
        server_result = await session.execute(
            Server.__table__.select().where(Server.id == config.server_id)
        )
        server = server_result.fetchone()
        if server:
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

        # Удаляем из БД бота
        await session.execute(Config.__table__.delete().where(Config.id == config_id))
        await session.commit()
        await callback.message.edit_text("✅ Конфиг удалён!")

@router.callback_query(F.data.startswith("admin_user_configs_"), admin_only())
async def admin_user_configs(callback: CallbackQuery):
    user_id = callback.data.split("_", 3)[-1]
    
    async with async_session_maker() as session:
        config_result = await session.execute(
            Config.__table__.select().where(Config.user_tg_id == user_id)
        )
        configs = config_result.fetchall()
        
        if not configs:
            await callback.message.edit_text(
                "❌ У пользователя нет конфигов.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin_users_search_{user_id}")]
                ])
            )
            return
        
        text = f"<b>📋 Конфиги пользователя {user_id}</b>\n\n"
        buttons = []
        for cfg in configs:
            status = "✅ Активен" if cfg.active else "❌ Неактивен"
            server_info = f" ({cfg.server_id})" if cfg.server_id else ""
            text += f"• <code>{cfg.id[:8]}...</code>{server_info}\n   {status}\n\n"
            buttons.append([
                InlineKeyboardButton(text="🔧", callback_data=f"admin_config_detail_{cfg.id}"),
                InlineKeyboardButton(text="🗑️", callback_data=f"admin_delete_config_{cfg.id}")
            ])
        
        buttons.append([InlineKeyboardButton(text="⬅️ Назад к профилю", callback_data=f"admin_users_list_0")])
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("admin_config_detail_"), admin_only())
async def admin_config_detail(callback: CallbackQuery):
    config_id = callback.data.split("_", 3)[-1]
    
    async with async_session_maker() as session:
        cfg = await session.get(Config, config_id)
        if not cfg:
            await callback.message.edit_text("❌ Конфиг не найден.")
            return
        
        # Получаем сервер (если есть)
        server_name = "—"
        if cfg.server_id:
            server_result = await session.execute(
                Server.__table__.select().where(Server.id == cfg.server_id)
            )
            server = server_result.fetchone()
            if server:
                server_name = f"{server.country} ({server.city})"
        
        used_gb = int(cfg.traffic_used_bytes) / (1024 ** 3) if cfg.traffic_used_bytes.isdigit() else 0
        limit_gb = cfg.traffic_limit_gb
        
        text = (
            f"<b>🔧 Конфиг: <code>{cfg.id}</code></b>\n\n"
            f"Сервер: {server_name}\n"
            f"Трафик: {used_gb:.1f} / {limit_gb} ГБ\n"
            f"Истекает: {cfg.expiry.split('T')[0] if cfg.expiry else '—'}\n"
            f"Статус: {'✅ Активен' if cfg.active else '❌ Неактивен'}"
        )
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Продлить", callback_data=f"admin_renew_{cfg.id}"),
                InlineKeyboardButton(text="🔄 Сбросить трафик", callback_data=f"admin_reset_traffic_{cfg.id}")
            ],
            [
                InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"admin_delete_config_{cfg.id}")
            ],
            [
                InlineKeyboardButton(text="⬅️ Назад к списку", callback_data=f"admin_user_configs_{cfg.user_tg_id}")
            ]
        ])
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)



@router.callback_query(F.data.startswith("promo_del_"), admin_only())
async def delete_promo(callback: CallbackQuery, state: FSMContext):
    promo_code_hash = callback.data.split("_", 2)[-1]
    
    from services.promocode_service import delete_promo as delete_promo_service
    await delete_promo_service(promo_code_hash)
    
    # Получаем сохранённую страницу (или 0 по умолчанию)
    data = await state.get_data()
    page = data.get("promo_page", 0)
    
    # Обновляем список на той же странице
    await _render_promo_list(callback.message, page)
    
    await callback.answer("✅ Промокод удалён")

# ==================== ОБРАБОТКА СООБЩЕНИЙ ОТ АДМИНА ====================


# ==================== ОБРАБОТКА СООБЩЕНИЙ ОТ АДМИНА ====================
@router.message(admin_only())
async def handle_admin_messages(message: Message):
    """Универсальный обработчик сообщений от админа."""
    text = message.text.strip()
    
    # 1. Попытка обработать как JSON сервера
    if text.startswith("{") and text.endswith("}"):
        try:
            server_data = json.loads(text)
            if "id" in server_data and "xui_url" in server_data:
                await _handle_server_json(message, server_data)
                return
        except json.JSONDecodeError:
            pass
    
    # 2. Попытка обработать как тариф: категория|дней|цена|трафик
    if "|" in text and len(text.split("|")) == 4:
        parts = text.split("|")
        # Проверяем, что все части — числа (кроме категории)
        try:
            days = int(parts[1])
            price = int(parts[2])
            traffic = int(parts[3])
            # Это тариф
            from services.tariff_service import create_tariff
            await create_tariff(parts[0], days, price, traffic)
            await message.answer("✅ Тариф добавлен!")
            return
        except ValueError:
            pass  # Не тариф, проверяем дальше
    
    # 3. Попытка обработать как промокод: код|тип|значение|макс_использований
    if "|" in text and len(text.split("|")) == 4:
        parts = text.split("|")
        # Проверяем тип промокода
        if parts[1] in ["fixed_days", "percent", "fixed_rub"]:
            try:
                value = int(parts[2])
                max_uses = int(parts[3])
                from services.promocode_service import create_promocode
                await create_promocode(parts[0], parts[1], value, max_uses)
                await message.answer(f"✅ Промокод <code>{parts[0]}</code> создан!", parse_mode=ParseMode.HTML)
                return
            except ValueError:
                pass  # Не промокод
    
    # 4. Попытка обработать как Telegram ID (поиск пользователя)
    try:
        user_id = int(text)
        await _handle_user_search(message, user_id)
        return
    except ValueError:
        pass
    
    # 5. Если ничего не подошло
    await message.answer("❓ Неизвестная команда. Используйте меню.")


async def _handle_server_json(message: Message, server_data: dict):

    """Обработка JSON для добавления сервера."""
    required_fields = ["id", "country", "city", "xui_url", "xui_username", "xui_password", "inbound_id"]
    for field in required_fields:
        if field not in server_data:
            await message.answer(f"❌ Отсутствует обязательное поле: {field}")
            return
    
    async with async_session_maker() as session:
        existing = await session.execute(
            Server.__table__.select().where(Server.id == server_data["id"])
        )
        if existing.fetchone():
            await message.answer("❌ Сервер с таким ID уже существует.")
            return
        
        await session.execute(Server.__table__.insert().values(
            id=server_data["id"],
            country=server_data["country"],
            city=server_data["city"],
            xui_url=server_data["xui_url"].rstrip("/"),
            xui_username=server_data["xui_username"],
            xui_password=server_data["xui_password"],
            inbound_id=str(server_data["inbound_id"]),
            mobile_spoof=bool(server_data.get("mobile_spoof", False)),
            subscription_path=server_data.get("subscription_path", "/sub"),
            subscription_port=str(server_data.get("subscription_port", 2096)),
            active=bool(server_data.get("active", True))
        ))
        await session.commit()
    
    await message.answer("✅ Сервер успешно добавлен!")


async def _handle_user_search(message: Message, user_id: int):
    """Поиск пользователя по Telegram ID."""
    async with async_session_maker() as session:
        user_result = await session.execute(
            User.__table__.select().where(User.tg_id == str(user_id))
        )
        user = user_result.fetchone()
        
        if not user:
            await message.answer("❌ Пользователь не найден.")
            return
        
        config_result = await session.execute(
            Config.__table__.select().where(Config.user_tg_id == str(user_id))
        )
        configs = config_result.fetchall()
        
        text = (
            f"<b>👤 Пользователь ID:</b> <code>{user_id}</code>\n"
            f"<b>Имя:</b> {user.first_name}\n"
            f"<b>Username:</b> @{user.username if user.username else '—'}\n"
            f"<b>Конфигов:</b> {len(configs)}\n\n"
        )
        
        if configs:
            text += "<b>Конфиги:</b>\n"
            for i, cfg in enumerate(configs, 1):
                status = "✅ Активен" if cfg.active else "❌ Неактивен"
                trial_mark = " 🆓 Trial" if cfg.base_tariff == "trial" else ""
                text += f"{i}. <code>{cfg.id}</code>{trial_mark}\n   Сервер: {cfg.server_id}\n   {status}\n"
        
        buttons = []
        if configs:
            # Берём первый активный конфиг (или любой)
            active_configs = [c for c in configs if c.active]
            if active_configs:
                cfg = active_configs[0]
                buttons.append([
                    InlineKeyboardButton(
                        text="📋 Конфиги",
                        callback_data=f"admin_user_configs_{user.tg_id}"
                    )
                ])

        buttons.append([InlineKeyboardButton(text="⬅️ Назад к списку", callback_data="admin_users_list_0")])
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)

@router.callback_query(F.data.startswith("promo_toggle_"), admin_only())
async def promo_toggle(callback: CallbackQuery):
    promo_code_hash = callback.data.split("_", 2)[-1]
    
    async with async_session_maker() as session:
        result = await session.execute(
            select(Promocode).where(Promocode.code_hash == promo_code_hash)
        )
        promo = result.scalar_one_or_none()
        if not promo:
            await callback.answer("Промокод не найден.", show_alert=True)
            return
        promo.active = not promo.active
        await session.commit()
    
    # Обновляем карточку
    fake_callback = type('obj', (object,), {
        'data': f"promo_detail_{promo_code_hash}",
        'message': callback.message,
        'answer': lambda *a, **kw: None
    })()
    await promo_detail(fake_callback)
