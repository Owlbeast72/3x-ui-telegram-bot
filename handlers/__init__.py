# handlers/__init__.py
from aiogram import Dispatcher
from .start import router as start_router
from .buy import router as buy_router
from .my_configs import router as my_configs_router
from .renew import router as renew_router
from .admin import router as admin_router
from .payment import router as payment_router
from .admin_panel import router as admin_panel_router  
from .promo import router as promo_router
from .settings import router as settings_router

def register_all_handlers(dp: Dispatcher):
    dp.include_router(promo_router)  
    dp.include_router(start_router)
    dp.include_router(buy_router)
    dp.include_router(my_configs_router)
    dp.include_router(renew_router)
    dp.include_router(admin_router)
    dp.include_router(payment_router)
    dp.include_router(admin_panel_router)  
    dp.include_router(settings_router)
