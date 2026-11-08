import html
import logging
from decimal import Decimal
from typing import Optional

from telegram import Message, MessageEntity, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

import db
import userbot
from config import ADMIN_IDS, BOT_TOKEN, DEAL_FEE
from form import DealForm, fmt_amount, parse_form, parse_number

logging.basicConfig(format="%(asctime)s %(name)s %(levelname)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("usdtmarketin")


def emoji(emoji_id: str, fallback: str) -> str:
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


E_TITLE = emoji("5409163361126476007", "🛡")
E_ID = emoji("5257965810634202885", "🆔")
E_ESCROWER = emoji("5260450573768990626", "👤")
E_ESCROWER_BADGE = emoji("5972185809300753162", "✅")
E_PARTY = emoji("5260399854500191689", "👤")
E_AMOUNT = emoji("5116648080787112958", "💵")
E_FEES = emoji("5260450573768990626", "💰")


def is_admin(user_id: Optional[int]) -> bool:
    return user_id in ADMIN_IDS


def remember_from_message(msg: Optional[Message]) -> None:
    if not msg:
        return
    for user in [msg.from_user, *(msg.new_chat_members or ())]:
        if user and not user.is_bot:
            db.remember_user(user.id, user.username, user.first_name)
    for ent in msg.entities or ():
        if ent.type == MessageEntity.TEXT_MENTION and ent.user:
            db.remember_user(ent.user.id, ent.user.username, ent.user.first_name)


async def track_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    remember_from_message(msg)
    if msg:
        remember_from_message(msg.reply_to_message)


async def resolve_user_id(username: str) -> Optional[int]:
    user_id = db.lookup_user_id(username)
    if user_id:
        return user_id
    user_id = await userbot.resolve_username(username)
    if user_id:
        db.remember_user(user_id, username)
    return user_id


def party_line(username: str, user_id: Optional[int]) -> str:
    uid = str(user_id) if user_id else "unknown"
    return f"@{html.escape(username)} <code>{uid}</code>"


def render_deal_card(
    deal_id: str, escrower_name: str, form: DealForm,
    seller_id: Optional[int], buyer_id: Optional[int], amount: Decimal,
) -> str:
    release = amount - DEAL_FEE
    return (
        f"{E_TITLE} Escrow Deal\n\n"
        f"{E_ID} <b>ID</b> - <code>{deal_id}</code>\n"
        f"{E_ESCROWER} <b>Escrower</b> - {html.escape(escrower_name)} {E_ESCROWER_BADGE}\n"
        f"{E_PARTY} <b>Seller</b> - {party_line(form.seller, seller_id)}\n"
        f"{E_PARTY} <b>Buyer</b> - {party_line(form.buyer, buyer_id)}\n"
        f"{E_AMOUNT} <b>Amount</b> - <code>{fmt_amount(amount)}</code>$\n"
        f"{E_FEES} <b>Total Fees</b> - <code>{DEAL_FEE:.2f}</code>$\n\n"
        f"📤 <code>{fmt_amount(release)}</code>$ to be released!"
    )


async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user or not is_admin(user.id):
        return

    form_msg = msg.reply_to_message
    if not form_msg:
        await msg.reply_text("Reply to a filled deal form with /add [received amount]")
        return

    form = parse_form(form_msg.text or form_msg.caption)
    if not form:
        await msg.reply_text(
            "That message is not a valid deal form. Seller, Buyer, Amount, Rate and Crypto Chain are required."
        )
        return

    amount = parse_number(" ".join(context.args)) if context.args else None
    if amount is None:
        await msg.reply_text("Usage: reply to the form with /add [received amount], e.g. /add 100")
        return
    if amount <= DEAL_FEE:
        await msg.reply_text(f"Received amount must be more than the {DEAL_FEE:.2f}$ fee.")
        return

    existing = db.deal_for_form(msg.chat_id, form_msg.message_id)
    if existing:
        await msg.reply_text(
            f"This form is already added as <code>{existing['deal_id']}</code>.",
            parse_mode=ParseMode.HTML,
        )
        return

    remember_from_message(form_msg)
    seller_id = await resolve_user_id(form.seller)
    buyer_id = await resolve_user_id(form.buyer)

    deal_id = db.create_deal(
        chat_id=msg.chat_id,
        form_msg_id=form_msg.message_id,
        escrower_id=user.id,
        escrower_name=user.first_name,
        seller_username=form.seller,
        seller_id=seller_id,
        buyer_username=form.buyer,
        buyer_id=buyer_id,
        amount=str(amount),
        fee=str(DEAL_FEE),
        rate=form.rate,
        time_text=form.time,
        chain=form.chain,
        terms=form.terms,
    )
    card = await form_msg.reply_text(
        render_deal_card(deal_id, user.first_name, form, seller_id, buyer_id, amount),
        parse_mode=ParseMode.HTML,
    )
    db.set_card_msg(deal_id, card.message_id)
    log.info("Deal %s created by %s in chat %s", deal_id, user.id, msg.chat_id)


async def post_init(app: Application) -> None:
    await userbot.start()


async def post_shutdown(app: Application) -> None:
    await userbot.stop()


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN missing in .env")
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.add_handler(MessageHandler(filters.ALL, track_users), group=-1)
    app.add_handler(CommandHandler("add", add_cmd))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
