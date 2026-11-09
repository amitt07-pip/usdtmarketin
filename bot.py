import html
import logging
from decimal import Decimal
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, MessageEntity, Update
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application, ApplicationHandlerStop, CallbackQueryHandler, ChatMemberHandler, CommandHandler, ContextTypes,
    MessageHandler,
    TypeHandler, filters,
)

import addresses
import db
import userbot
from config import ADMIN_IDS, BOT_TOKEN, ALLOWED_GROUP_IDS, CHAT_GROUP_ID, DEAL_FEE, ESCROW_GROUP_IDS, JOIN_LOG_CHANNEL_ID
from form import DealForm, fmt_amount, parse_form, parse_number, parse_stats

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

E_STATS = emoji("5258362837411045098", "📊")
E_USERNAME = emoji("5388624995020708381", "👤")
E_TOTAL = emoji("5364138617667078875", "🤝")
E_RANK = emoji("5931472654660800739", "🏆")
E_WORTH = emoji("5971944878815317190", "💰")
E_AVG_TIME = emoji("5258419835922030550", "⏱")
E_MAX = emoji("5280625405252870672", "📈")
E_RATE = emoji("5197474765387864959", "✅")

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


async def only_escrow_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat and chat.type != "private" and chat.id not in ALLOWED_GROUP_IDS:
        raise ApplicationHandlerStop


async def track_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    remember_from_message(msg)
    if msg:
        remember_from_message(msg.reply_to_message)


def _is_present(member) -> bool:
    if member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
        return False
    if member.status == ChatMemberStatus.RESTRICTED:
        return bool(getattr(member, "is_member", False))
    return True


def user_tag(u) -> str:
    if u.username:
        return f"@{html.escape(u.username)}"
    return f'<a href="tg://user?id={u.id}">{html.escape(u.full_name or str(u.id))}</a>'


async def chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cmu = update.chat_member
    if not cmu or cmu.chat.id != CHAT_GROUP_ID:
        return
    was, now = _is_present(cmu.old_chat_member), _is_present(cmu.new_chat_member)
    user, actor = cmu.new_chat_member.user, cmu.from_user
    if user.is_bot or was == now:
        return
    if user.username:
        db.remember_user(user.id, user.username)

    if now:
        text = (
            f"~ {user_tag(user)} (<code>{user.id}</code>) has been added by "
            f"{user_tag(actor)} (<code>{actor.id}</code>) in the <b>UsdtMarketin</b> Group ‼️"
        )
        try:
            sent = await context.bot.send_message(
                JOIN_LOG_CHANNEL_ID, text, parse_mode=ParseMode.HTML
            )
        except TelegramError as exc:
            log.warning("Join notice for %s failed: %s", user.id, exc)
            return
        db.save_join_notice(user.id, JOIN_LOG_CHANNEL_ID, sent.message_id, text)
    else:
        notice = db.pop_join_notice(user.id)
        if not notice:
            return
        try:
            await context.bot.edit_message_text(
                f"<s>{notice['text']}</s>", chat_id=notice["channel_id"], message_id=notice["msg_id"],
                parse_mode=ParseMode.HTML,
            )
        except TelegramError as exc:
            log.warning("Strike join notice for %s failed: %s", user.id, exc)


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
    return _is_present(member)


def render_not_in_group(missing: list[tuple[str, str]]) -> tuple[str, InlineKeyboardMarkup]:
    lines = ["🚫 <b>Deal not created</b>"]
    lines += [f"• {role} @{html.escape(username)} — not in <b>Escrow group</b>" for role, username in missing]
    buttons = [
        InlineKeyboardButton(f"Contact {role}", url=f"https://t.me/{username}") for role, username in missing
    ]
    return "\n".join(lines), InlineKeyboardMarkup([buttons])


def party_line(username: str, user_id: Optional[int]) -> str:
    uid = str(user_id) if user_id else "unknown"
    return f"@{html.escape(username)} [<code>{uid}</code>]"


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
        f"{E_FEES} <code>{fmt_amount(release)}</code>$ to be released!"
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
    if kind == "Release":
        db.mark_released(deal["deal_id"])
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
        f"<code>Vouch @{VOUCH_CHANNEL} for ${fmt_amount(amount)} deal, safely escrowed.</code>"
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


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h" if hours else f"{days}d"


def fmt_usd(value: Decimal) -> str:
    return "0$" if value == 0 else f"{fmt_amount(value)}$"


async def compute_stats(context: ContextTypes.DEFAULT_TYPE, chat, user_id: int) -> dict:
    deals = db.user_deals(user_id)
    completed = [d for d in deals if d["status"] == "completed"]
    amounts = [Decimal(d["closed_amount"] or d["amount"]) for d in completed]
    durations = [
        (d["released_at"] or d["closed_at"]) - d["created_at"]
        for d in completed if (d["released_at"] or d["closed_at"])
    ]
    base = db.get_cloned_stats(user_id)

    board = db.completed_worth_by_user()
    if base:
        rank = base["rank"]
    elif user_id in board:
        mine = board[user_id]
        rank = 1 + sum(1 for uid, v in board.items() if uid != user_id and (v[1], v[0]) > (mine[1], mine[0]))
    else:
        rank = len(board) + 1
        if CHAT_GROUP_ID:
            try:
                rank = max(rank, await context.bot.get_chat_member_count(CHAT_GROUP_ID))
            except TelegramError:
                pass

    total = len(completed)
    worth = sum(amounts, Decimal(0))
    max_amount = max(amounts, default=Decimal(0))
    time_sum, time_count = sum(durations), len(durations)
    rate = round(100 * len(completed) / len(deals)) if deals else 0
    if base:
        total += base["total"]
        worth += Decimal(base["worth"])
        max_amount = max(max_amount, Decimal(base["max_amount"]))
        if base["avg_seconds"] is not None and base["total"]:
            time_sum += base["avg_seconds"] * base["total"]
            time_count += base["total"]
        if deals:
            base_all = round(base["total"] * 100 / base["rate"]) if base["rate"] else base["total"]
            base_done = base["total"] if base["rate"] else 0
            rate = round(100 * (base_done + len(completed)) / max(base_all + len(deals), 1))
        else:
            rate = base["rate"]

    return {
        "total": total,
        "rank": rank,
        "worth": worth,
        "avg": fmt_duration(time_sum / time_count) if time_count else "N/A",
        "max": max_amount,
        "rate": rate,
    }


def render_stats(username: Optional[str], user_id: int, name: Optional[str], st: dict) -> str:
    who = f"@{html.escape(username)}" if username else html.escape(name or "User")
    return (
        f"{E_STATS} <b>User Stats</b>\n\n"
        f"{E_USERNAME} <b>Username: {who}</b> [{user_id}]\n"
        f"{E_TOTAL} <b>Total Escrows:</b> <code>{st['total']}</code>\n"
        f"{E_RANK} <b>Ranking:</b> <code>{ordinal(st['rank'])}</code>\n"
        f"{E_WORTH} <b>Total Worth:</b> <code>{fmt_usd(st['worth'])}</code>\n"
        f"{E_AVG_TIME} <b>Average Time:</b> <code>{st['avg']}</code>\n"
        f"{E_MAX} <b>Max Escrow Amount:</b> <code>{fmt_usd(st['max'])}</code>\n"
        f"{E_RATE} <b>Completion Rate:</b> <code>{st['rate']}%</code>"
    )


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    target_id, username, name = user.id, user.username, user.first_name
    if context.args and context.args[0].startswith("@"):
        username = context.args[0].lstrip("@")
        target_id = await resolve_user_id(username)
        name = None
        if not target_id:
            await msg.reply_text(f"User @{html.escape(username)} not found.", parse_mode=ParseMode.HTML)
            return
    elif msg.reply_to_message and msg.reply_to_message.from_user and not msg.reply_to_message.from_user.is_bot:
        target = msg.reply_to_message.from_user
        target_id, username, name = target.id, target.username, target.first_name

    st = await compute_stats(context, update.effective_chat, target_id)
    await msg.reply_text(render_stats(username, target_id, name, st), parse_mode=ParseMode.HTML)


STATS_TEMPLATE = (
    "Total Escrows: 0\n"
    "Ranking: 1st\n"
    "Total Worth: 0$\n"
    "Average Time: 5m\n"
    "Max Escrow Amount: 0$\n"
    "Completion Rate: 100%"
)


async def clonestats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user or not is_admin(user.id):
        return

    target_id: Optional[int] = None
    username: Optional[str] = None
    name: Optional[str] = None
    arg = context.args[0].strip() if context.args else ""
    if arg.startswith("@"):
        username = arg.lstrip("@")
        target_id = await resolve_user_id(username)
        if not target_id:
            await msg.reply_text(f"User @{html.escape(username)} not found.", parse_mode=ParseMode.HTML)
            return
    elif arg.lstrip("-").isdigit():
        target_id = int(arg)
        username = db.username_for(target_id)
    elif msg.reply_to_message and msg.reply_to_message.from_user and not msg.reply_to_message.from_user.is_bot:
        target = msg.reply_to_message.from_user
        target_id, username, name = target.id, target.username, target.first_name
    else:
        await msg.reply_text("Usage: /clonestats @username | user id | reply to the user")
        return

    context.chat_data.setdefault("clonestats", {})[user.id] = {
        "id": target_id, "username": username, "name": name,
    }
    who = f"@{html.escape(username)}" if username else html.escape(name or "user")
    await msg.reply_text(
        f"Send the stats message for {who} <code>{target_id}</code> with filled values, e.g.\n\n"
        f"<code>{STATS_TEMPLATE}</code>",
        parse_mode=ParseMode.HTML,
    )


async def clonestats_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user or not is_admin(user.id):
        return
    pending = context.chat_data.get("clonestats", {})
    target = pending.get(user.id)
    if not target:
        return

    values, bad = parse_stats(msg.text)
    if bad:
        await msg.reply_text(
            f"Could not read: {', '.join(bad)}. Send the stats message again.",
        )
        return

    pending.pop(user.id, None)
    db.save_cloned_stats(target["id"], values, user.id)
    st = await compute_stats(context, update.effective_chat, target["id"])
    await msg.reply_text(
        "✅ Stats saved.\n\n" + render_stats(target["username"], target["id"], target["name"], st),
        parse_mode=ParseMode.HTML,
    )
    log.info("Admin %s cloned stats to %s: %s", user.id, target["id"], values)


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
    app.add_handler(TypeHandler(Update, only_escrow_group), group=-2)
    app.add_handler(MessageHandler(filters.ALL, track_users), group=-1)
    app.add_handler(ChatMemberHandler(chat_member_update, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(CommandHandler("add", add_cmd, filters=filters.Chat(ESCROW_GROUP_IDS)))
    app.add_handler(CommandHandler("release", release_cmd, filters=filters.Chat(ESCROW_GROUP_IDS)))
    app.add_handler(CommandHandler("refund", refund_cmd, filters=filters.Chat(ESCROW_GROUP_IDS)))
    app.add_handler(CommandHandler("close", close_cmd, filters=filters.Chat(ESCROW_GROUP_IDS)))
    app.add_handler(CommandHandler("verify", verify_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd, filters=filters.Chat(CHAT_GROUP_ID)))
    app.add_handler(CommandHandler("clonestats", clonestats_cmd))
    app.add_handler(CommandHandler("setupaddy", setupaddy_cmd))
    app.add_handler(CommandHandler("deleteaddy", deleteaddy_cmd))
    app.add_handler(CallbackQueryHandler(addy_callback, pattern=r"^addy:"))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, addy_text))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, clonestats_text), group=1)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
