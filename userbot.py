import logging
from typing import Optional

from config import API_HASH, API_ID, USERBOT_SESSION

log = logging.getLogger(__name__)
_client = None


async def start() -> None:
    global _client
    if not (API_ID and API_HASH):
        log.info("Userbot disabled (API_ID/API_HASH not set)")
        return
    from telethon import TelegramClient

    client = TelegramClient(USERBOT_SESSION, API_ID, API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        log.warning("Userbot session not logged in; run: python login_userbot.py")
        await client.disconnect()
        return
    me = await client.get_me()
    log.info("Userbot connected as %s (%s)", me.username or me.first_name, me.id)
    _client = client


async def stop() -> None:
    if _client:
        await _client.disconnect()


async def resolve_username(username: str) -> Optional[int]:
    if not _client:
        return None
    try:
        from telethon.tl.types import User

        entity = await _client.get_entity(username.lstrip("@"))
        return entity.id if isinstance(entity, User) else None
    except Exception as exc:
        log.info("Userbot could not resolve @%s: %s", username, exc)
        return None
