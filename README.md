# usdtmarketin

Telegram escrow transaction manager bot (python-telegram-bot 22.8 + Telethon userbot).

## Flow

1. Seller/buyer post the deal form in the group:
   ```
   Seller - @seller_username
   Buyer - @buyer_username
   Amount in $ - 100
   Rate - 110
   Time to Complete deal - ...
   [MUST] Crypto Chain - ...

   Mention terms [ if any, don't cry later] : ...
   ```
   Labels are case-insensitive; Seller, Buyer, Amount, Rate and Chain are required.
2. An admin (`ADMIN_IDS`) replies to the form with `/add <received amount>`.
   The bot replies to the form with the deal card (`DL-MRKTIN<number>`, fixed `DEAL_FEE`).
   Non-admins get no response.
   If the seller and/or buyer is not a member of the group, the bot replies
   "🚫 Deal not created" listing only the missing party with "Contact Seller"/"Contact Buyer" buttons.
3. The seller replies to the Escrow Deal message with `/release`. The bot posts a Release Request
   (deal ID, release amount, seller/buyer/escrow) with a "View Deal Form" link to that deal message.
   Vice versa, the buyer can send `/refund` for a Refund Request. Both are notifications only.
4. An admin replies to the Escrow Deal message with `/close [amount]` (amount defaults to the received amount).
   The bot posts the closed/vouch message and marks the deal `completed`; later `/close` or `/release`
   on it get "❌ No ongoing deals found for you…".

## Stats

`/stats` (anyone): own stats; reply to a user or `/stats @username` for theirs. Counts completed (closed)
deals where the user was seller or buyer. Ranking is by total worth; users with no deals are ranked last
(group member count). Average time is `/add` → first `/release` (or `/close` if no release).

`/clonestats @username | user id | reply` (escrowers): the bot asks for a filled stats message, reads
Total Escrows / Ranking / Total Worth / Average Time / Max Escrow Amount / Completion Rate from its lines
(a pasted /stats message works) and saves them as that user's base stats. Later completed deals add on top
(ranking stays the cloned value).

The bot only works in `ESCROW_GROUP_ID` (default `-1002905230936`) and in DMs; it ignores every other group.
`/add`, `/release`, `/refund` and `/close` only work in the escrow group.

## Escrower addresses

- `/setupaddy` (escrowers, bot DM only): pick token (USDT) → network (BSC/BEP20, Polygon, ETH/ERC20, TRX/TRC20)
  → send the address. Format is validated (EVM `0x` + 40 hex, Tron base58check). One address per network per
  escrower; an address locked by one escrower can't be locked by another.
- `/deleteaddy` (escrowers, bot DM): deletes the locked address (buttons to choose if several networks).
- `/verify <address>` (anyone): VERIFIED ADDRESS card (type, owner, escrower ID) or NOT VERIFIED.

Seller/buyer user IDs come from a local cache of every user the bot sees in its groups,
then from the userbot (`get_entity`). Turn the bot's group privacy off (BotFather → /setprivacy)
or make it admin so it sees all messages. The premium emoji need the bot owner to have Premium.

## Setup

```bash
git clone -b <branch> https://github.com/amitt07-pip/usdtmarketin /root/usdtmarketin
cd /root/usdtmarketin
pip install -r requirements.txt
cp .env.example .env && nano .env
python3 login_userbot.py        # one-time userbot login (phone + code)
cp usdtmarketin.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now usdtmarketin
journalctl -u usdtmarketin -f
```
