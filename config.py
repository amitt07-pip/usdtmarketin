import os
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _ids(value: str) -> set[int]:
    return {int(x) for x in value.replace(" ", "").split(",") if x}


BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = _ids(os.getenv("ADMIN_IDS", ""))
DEAL_FEE = Decimal(os.getenv("DEAL_FEE", "1.00"))
DEAL_ID_PREFIX = "DL-MRKTIN"
DEAL_ID_START = int(os.getenv("DEAL_ID_START", "10001"))
ESCROW_GROUP_IDS = _ids(os.getenv("ESCROW_GROUP_IDS", "") or "-1002905230936,-1004331931828")
CHAT_GROUP_ID = int(os.getenv("CHAT_GROUP_ID", "") or "-1003916239874")
ALLOWED_GROUP_IDS = ESCROW_GROUP_IDS | {CHAT_GROUP_ID}
DB_PATH = BASE_DIR / os.getenv("DB_FILE", "escrow.db")

API_ID = int(os.getenv("API_ID", "0") or 0)
API_HASH = os.getenv("API_HASH", "")
USERBOT_SESSION = str(BASE_DIR / os.getenv("USERBOT_SESSION", "userbot_session"))
