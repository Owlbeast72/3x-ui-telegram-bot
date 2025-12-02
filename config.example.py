import os

# Токены и ID
BOT_TOKEN = "your-bot-token"
CRYPTO_PAY_API_TOKEN = "your-cryptobot-token"
ADMIN_TELEGRAM_ID = your-tgID

# Пути
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SSL_CERTS_DIR = os.path.join(BASE_DIR, "ssl-certs")
CONFIG_DIR = os.path.join(BASE_DIR, "config")
SERVERS_FILE = os.path.join(CONFIG_DIR, "servers.json")
SUBS_FILE = os.path.join(CONFIG_DIR, "subscriptions.json")
PENDING_FILE = os.path.join(CONFIG_DIR, "pending_payments.json")

# Убедимся, что папка config существует
os.makedirs(CONFIG_DIR, exist_ok=True)

# Цены
STABLE_BASE_PRICES = {
    "1w": 100,
    "1m": 400,
    "2m": 800,
    "3m": 1200,
    "6m": 2400,
    "1y": 4800
}

MOBILE_BASE_PRICES = {
    "1w": 150,
    "1m": 600,
    "2m": 1200,
    "3m": 1800
}

class TrialConfig:
    ENABLED = True
    DEFAULT_DAYS = 1        # Обычный Trial
    TRAFFIC_GB = 10
    DEVICES = 1
