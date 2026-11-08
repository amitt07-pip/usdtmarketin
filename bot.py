import html
import logging
from decimal import Decimal
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, MessageEntity, Update
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters,
)

import addresses
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
E_RELEASE = emoji("5395695537687123235", "📤")
E_CLOSED = emoji("6296367896398399651", "✅")

E_ADDRESS = emoji("5197288647275071607", "📍")
E_TYPE = emoji("5877597667231534929", "🪙")
E_BELONGS = emoji("5886412370347036129", "👤")
E_ESCROWER_ID = emoji("5841276284155467413", "🆔")
E_UNKNOWN = emoji("5213195952008997792", "⚠️")
E_MASKED = emoji("5240241223632954241", "📍")
E_NOT_REGISTERED = emoji("5019523782004441717", "❌")

VOUCH_CHANNEL = "usdtmarketin"
NO_ONGOING_DEALS = "<i>❌ No ongoing deals found for you. Create or accept a deal first.</i>"


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


async def in_group(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: Optional[int]) -> bool:
    if not user_id:
        return False
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
    except TelegramError as exc:
        log.info("get_chat_member(%s, %s) failed: %s", chat_id, user_id, exc)
        return False
    if member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
        return False
    if member.status == ChatMemberStatus.RESTRICTED:
        return bool(getattr(member, "is_member", False))
    return True


def render_not_in_group(missing: list[tuple[str, str]]) -> tuple[str, InlineKeyboardMarkup]:
    lines = ["🚫 <b>Deal not created</b>"]
    lines += [f"• {role} @{html.escape(username)} — not in <b>Escrow group</b>" for role, username in missing]
    buttons = [
        InlineKeyboardButton(f"Contact {role}", url=f"https://t.me/{username}") for role, username in missing
    ]
    return "\n".join(lines), InlineKeyboardMarkup([buttons])


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

    missing = [
        (role, username)
        for role, username, uid in (("Seller", form.seller, seller_id), ("Buyer", form.buyer, buyer_id))
        if not await in_group(context, msg.chat_id, uid)
    ]
    if missing:
        text, markup = render_not_in_group(missing)
        await form_msg.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
        return

    deal_id = db.create_deal(
        chat_id=msg.chat_id,
        form_msg_id=form_msg.message_id,
        escrower_id=user.id,
        escrower_name=user.first_name,
        escrower_username=user.username,
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


def message_link(chat, msg_id: int) -> str:
    if chat.username:
        return f"https://t.me/{chat.username}/{msg_id}"
    internal_id = str(chat.id).removeprefix("-100").lstrip("-")
    return f"https://t.me/c/{internal_id}/{msg_id}"


def user_ref(username: Optional[str], user_id: Optional[int], name: Optional[str]) -> str:
    if username:
        return f"@{html.escape(username)}"
    return f'<a href="tg://user?id={user_id}">{html.escape(name or str(user_id))}</a>'


def render_request(deal, kind: str) -> str:
    amount = Decimal(deal["amount"])
    fee = Decimal(deal["fee"])
    return (
        f"{E_RELEASE} <b>{kind} Request</b>\n\n"
        f"<code>{deal['deal_id']}</code> | ${fmt_amount(amount - fee)} (after {fmt_amount(fee).removesuffix('.0')}$ fees)\n"
        f"<b>Seller:</b> @{html.escape(deal['seller_username'])}\n"
        f"<b>Buyer:</b> @{html.escape(deal['buyer_username'])}\n"
        f"<b>Escrow:</b> {user_ref(deal['escrower_username'], deal['escrower_id'], deal['escrower_name'])}\n\n"
        f"<i>Review and {kind} if valid</i>"
    )


async def request_cmd(update: Update, kind: str, party: str) -> None:
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    card_msg = msg.reply_to_message
    deal = db.deal_for_card(msg.chat_id, card_msg.message_id) if card_msg else None
    if not deal:
        return
    if user.id != deal[f"{party}_id"] or deal["status"] == "completed":
        await msg.reply_text(NO_ONGOING_DEALS, parse_mode=ParseMode.HTML)
        return

    markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("View Deal Form", url=message_link(msg.chat, deal["card_msg_id"]))]]
    )
    await msg.reply_text(render_request(deal, kind), parse_mode=ParseMode.HTML, reply_markup=markup)
    db.set_status(deal["deal_id"], f"{kind.lower()}_requested")
    log.info("%s requested for %s by %s", kind, deal["deal_id"], user.id)


async def release_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await request_cmd(update, "Release", "seller")


async def refund_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await request_cmd(update, "Refund", "buyer")


def render_closed(deal, amount: Decimal) -> str:
    return (
        f"{E_CLOSED} <b>Deal {deal['deal_id']} has been closed!</b>\n"
        f"~ @{html.escape(deal['seller_username'])} and @{html.escape(deal['buyer_username'])} "
        f"are requested to drop the vouch before leaving.\n\n"
        f"<b>Vouch @{VOUCH_CHANNEL} for ${fmt_amount(amount)} deal, safely escrowed.</b>"
    )


async def close_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user or not is_admin(user.id):
        return
    card_msg = msg.reply_to_message
    deal = db.deal_for_card(msg.chat_id, card_msg.message_id) if card_msg else None
    if not deal:
        return
    if deal["status"] == "completed":
        await msg.reply_text(NO_ONGOING_DEALS, parse_mode=ParseMode.HTML)
        return

    amount = parse_number(" ".join(context.args)) if context.args else None
    if amount is None:
        amount = Decimal(deal["amount"])
    db.close_deal(deal["deal_id"], str(amount), user.id)
    await msg.reply_text(render_closed(deal, amount), parse_mode=ParseMode.HTML)
    log.info("Deal %s closed by %s for %s", deal["deal_id"], user.id, amount)


def render_verified(display: str, records) -> str:
    owner = records[0]
    types = " / ".join(addresses.type_label(r["token"], r["network"]) for r in records)
    return (
        f"{E_CLOSED} <b>VERIFIED ADDRESS</b>\n\n"
        f"{E_ADDRESS} <b>Address:</b> <code>{html.escape(display)}</code>\n"
        f"{E_TYPE} <b>Type:</b> <code>{types}</code>\n"
        f'{E_BELONGS} <b>Belongs To:</b> <a href="tg://user?id={owner["admin_id"]}">'
        f"{html.escape(owner['admin_name'] or str(owner['admin_id']))}</a>\n"
        f"{E_ESCROWER_ID} <b>Escrower ID:</b> <code>{owner['admin_id']}</code>\n\n"
        f"{E_CLOSED} <i>This address is officially registered and safe to use.</i>"
    )


def render_not_verified(display: str) -> str:
    masked = f"{display[:4]}****{display[-4:]}" if len(display) > 8 else display
    return (
        f"{E_UNKNOWN} <b>UNKNOWN ADDRESS</b>\n\n"
        f"{E_MASKED} {html.escape(masked)}\n\n"
        f"{E_NOT_REGISTERED} <i>This address is NOT registered to any of our escrowers.</i>"
    )


async def verify_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg:
        return
    if not context.args:
        await msg.reply_text("Usage: /verify [address]")
        return
    display = context.args[0].strip()
    records = db.address_records(addresses.normalize(display))
    if records:
        await msg.reply_text(render_verified(records[0]["display_address"], records), parse_mode=ParseMode.HTML)
    else:
        await msg.reply_text(render_not_verified(display), parse_mode=ParseMode.HTML)


def _private_admin(update: Update) -> bool:
    chat = update.effective_chat
    user = update.effective_user
    return bool(chat and user and chat.type == "private" and is_admin(user.id))


async def setupaddy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _private_admin(update):
        return
    context.user_data.pop("addy_setup", None)
    markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton(token, callback_data=f"addy:token:{token}")] for token in addresses.TOKENS]
    )
    await update.effective_message.reply_text("Select token:", reply_markup=markup)


async def addy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _private_admin(update):
        await query.answer()
        return
    _, step, value = query.data.split(":", 2)
    if step == "token" and value in addresses.TOKENS:
        context.user_data["addy_setup"] = {"token": value}
        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton(label, callback_data=f"addy:net:{key}")]
             for key, (label, _) in addresses.NETWORKS.items()]
        )
        await query.edit_message_text(f"Token: {value}\nSelect network:", reply_markup=markup)
    elif step == "net" and value in addresses.NETWORKS:
        setup = context.user_data.get("addy_setup")
        if not setup:
            await query.answer("Send /setupaddy again", show_alert=True)
            return
        if any(r["network"] == value for r in db.admin_addresses(update.effective_user.id)):
            context.user_data.pop("addy_setup", None)
            await query.edit_message_text(
                f"You already have a locked {addresses.NETWORKS[value][0]} address. Use /deleteaddy first."
            )
            await query.answer()
            return
        setup["network"] = value
        await query.edit_message_text(
            f"Token: {setup['token']}\nNetwork: {addresses.NETWORKS[value][0]}\n\nSend your address now."
        )
    elif step == "del":
        if db.delete_address(update.effective_user.id, value):
            await query.edit_message_text(f"Deleted your {addresses.NETWORKS[value][0]} address.")
        else:
            await query.edit_message_text("No locked address found for that network.")
    await query.answer()


async def addy_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _private_admin(update):
        return
    setup = context.user_data.get("addy_setup")
    if not setup or "network" not in setup:
        return
    msg = update.effective_message
    user = update.effective_user
    display = msg.text.strip()
    network, token = setup["network"], setup["token"]
    if not addresses.is_valid(network, display):
        await msg.reply_text(f"Invalid {addresses.NETWORKS[network][0]} address. Send a valid address.")
        return
    key = addresses.normalize(display)
    owner = db.address_owner(key)
    if owner and owner != user.id:
        context.user_data.pop("addy_setup", None)
        await msg.reply_text("This address is already locked by another escrower.")
        return
    if any(r["network"] == network for r in db.admin_addresses(user.id)):
        context.user_data.pop("addy_setup", None)
        await msg.reply_text("You already have a locked address on this network. Use /deleteaddy first.")
        return
    db.lock_address(key, display, network, token, user.id, user.first_name)
    context.user_data.pop("addy_setup", None)
    await msg.reply_text(
        f"🔒 Address locked\n\n<b>Type:</b> <code>{addresses.type_label(token, network)}</code>\n"
        f"<b>Address:</b> <code>{html.escape(display)}</code>",
        parse_mode=ParseMode.HTML,
    )
    log.info("Admin %s locked %s address %s", user.id, network, display)


async def deleteaddy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _private_admin(update):
        return
    msg = update.effective_message
    context.user_data.pop("addy_setup", None)
    rows = db.admin_addresses(update.effective_user.id)
    if not rows:
        await msg.reply_text("You have no locked address. Use /setupaddy to set one.")
        return
    if len(rows) == 1:
        row = rows[0]
        db.delete_address(row["admin_id"], row["network"])
        await msg.reply_text(
            f"Deleted your <code>{addresses.type_label(row['token'], row['network'])}</code> address "
            f"<code>{html.escape(row['display_address'])}</code>. Use /setupaddy to set a new one.",
            parse_mode=ParseMode.HTML,
        )
        return
    markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton(
            f"{addresses.type_label(r['token'], r['network'])} {r['display_address'][:6]}…{r['display_address'][-4:]}",
            callback_data=f"addy:del:{r['network']}",
        )] for r in rows]
    )
    await msg.reply_text("Select the address to delete:", reply_markup=markup)


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
    app.add_handler(CommandHandler("release", release_cmd))
    app.add_handler(CommandHandler("refund", refund_cmd))
    app.add_handler(CommandHandler("close", close_cmd))
    app.add_handler(CommandHandler("verify", verify_cmd))
    app.add_handler(CommandHandler("setupaddy", setupaddy_cmd))
    app.add_handler(CommandHandler("deleteaddy", deleteaddy_cmd))
    app.add_handler(CallbackQueryHandler(addy_callback, pattern=r"^addy:"))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, addy_text))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
