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
