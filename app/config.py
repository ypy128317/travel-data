import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

DB_CONFIG = {
    "host": os.environ["DB_HOST"],
    "port": int(os.environ["DB_PORT"]),
    "user": os.environ["DB_USER"],
    "password": os.environ["DB_PASSWORD"],
    "database": os.environ["DB_NAME"],
    "charset": "utf8mb4",
    "autocommit": False,
}

APP_PORT = int(os.environ["APP_PORT"])
DEMO_PAYMENT_SIGNATURE = os.environ.get("DEMO_PAYMENT_SIGNATURE", "mock-payment-signature")

# 通义千问（DashScope OpenAI 兼容模式）
DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
DASHSCOPE_BASE_URL = os.environ.get(
    "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
).rstrip("/")
MODEL_NAME = os.environ.get("MODEL_NAME", "qwen-flash")

# 腾讯地图 Key（后端注入，不暴露到前端源码）
TMAP_KEY = os.environ.get("TMAP_KEY", "").strip()
