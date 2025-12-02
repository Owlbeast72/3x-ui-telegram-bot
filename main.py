import asyncio
import logging
import sys
import signal
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from config import BOT_TOKEN
from handlers import register_all_handlers
from tasks.expiration_checker import deactivate_expired_subscriptions, reset_monthly_traffic
from tasks.notifications import send_subscription_notifications, send_traffic_notifications
from tasks.traffic_updater import update_all_traffic
from storage.database import init_db, async_engine

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger(__name__)


async def main():
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    
    await init_db()
    register_all_handlers(dp)
    dp["bot"] = bot

    # Запускаем фоновые задачи и сохраняем ссылки на них
    background_tasks = [
        asyncio.create_task(deactivate_expired_subscriptions(), name="deactivate_expired"),
        asyncio.create_task(update_all_traffic(), name="update_traffic"),
        asyncio.create_task(reset_monthly_traffic(), name="reset_traffic"),
        asyncio.create_task(send_subscription_notifications(bot), name="notify_subscriptions"),
        asyncio.create_task(send_traffic_notifications(bot), name="notify_traffic"),
    ]

    # На Linux/macOS добавляем обработчики сигналов для graceful shutdown
    if sys.platform != "win32":
        def _signal_handler():
            logger.info("Получен сигнал завершения. Отмена фоновых задач...")
            for task in background_tasks:
                if not task.done():
                    task.cancel()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)

    try:
        await dp.start_polling(bot)
    finally:
        # Отменяем все фоновые задачи
        for task in background_tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.warning(f"Ошибка при отмене задачи {task.get_name()}: {e}")

        # Корректно закрываем пул соединений с БД
        await async_engine.dispose()
        logger.info("Соединения с базой данных закрыты.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем.")
