"""One-time userbot login: python login_userbot.py"""
from telethon.sync import TelegramClient

from config import API_HASH, API_ID, USERBOT_SESSION

if not (API_ID and API_HASH):
    raise SystemExit("Set API_ID and API_HASH in .env first (https://my.telegram.org)")

with TelegramClient(USERBOT_SESSION, API_ID, API_HASH) as client:
    me = client.get_me()
    print(f"Logged in as {me.first_name} (@{me.username}) id={me.id}")
