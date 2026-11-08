import sqlite3
import time
from typing import Optional

from config import DB_PATH, DEAL_ID_PREFIX, DEAL_ID_START

_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.row_factory = sqlite3.Row
_conn.executescript(
    """
    CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        first_name TEXT,
        updated_at INTEGER
    );
    CREATE TABLE IF NOT EXISTS deals (
        num INTEGER PRIMARY KEY,
        deal_id TEXT UNIQUE NOT NULL,
        chat_id INTEGER NOT NULL,
        form_msg_id INTEGER NOT NULL,
        card_msg_id INTEGER,
        escrower_id INTEGER NOT NULL,
        escrower_name TEXT,
        seller_username TEXT,
        seller_id INTEGER,
        buyer_username TEXT,
        buyer_id INTEGER,
        amount TEXT NOT NULL,
        fee TEXT NOT NULL,
        rate TEXT,
        time_text TEXT,
        chain TEXT,
        terms TEXT,
        status TEXT NOT NULL DEFAULT 'active',
        created_at INTEGER NOT NULL,
        UNIQUE (chat_id, form_msg_id)
    );
    CREATE TABLE IF NOT EXISTS addresses (
        address TEXT NOT NULL,
        network TEXT NOT NULL,
        token TEXT NOT NULL,
        admin_id INTEGER NOT NULL,
        admin_name TEXT,
        display_address TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        PRIMARY KEY (address, network),
        UNIQUE (admin_id, network)
    );
    """
)
_deal_cols = {r["name"] for r in _conn.execute("PRAGMA table_info(deals)")}
for _col, _type in (
    ("escrower_username", "TEXT"), ("closed_amount", "TEXT"), ("closed_by", "INTEGER"),
    ("released_at", "INTEGER"), ("closed_at", "INTEGER"),
):
    if _col not in _deal_cols:
        _conn.execute(f"ALTER TABLE deals ADD COLUMN {_col} {_type}")
_conn.commit()


def remember_user(user_id: int, username: Optional[str], first_name: Optional[str] = None) -> None:
    if not username:
        return
    _conn.execute(
        "INSERT INTO users (username, user_id, first_name, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(username) DO UPDATE SET user_id=excluded.user_id, "
        "first_name=excluded.first_name, updated_at=excluded.updated_at",
        (username.lower(), user_id, first_name, int(time.time())),
    )
    _conn.commit()


def lookup_user_id(username: str) -> Optional[int]:
    row = _conn.execute(
        "SELECT user_id FROM users WHERE username = ?", (username.lower().lstrip("@"),)
    ).fetchone()
    return row["user_id"] if row else None


def deal_for_form(chat_id: int, form_msg_id: int) -> Optional[sqlite3.Row]:
    return _conn.execute(
        "SELECT * FROM deals WHERE chat_id = ? AND form_msg_id = ?", (chat_id, form_msg_id)
    ).fetchone()


def deal_for_card(chat_id: int, card_msg_id: int) -> Optional[sqlite3.Row]:
    return _conn.execute(
        "SELECT * FROM deals WHERE chat_id = ? AND card_msg_id = ?", (chat_id, card_msg_id)
    ).fetchone()


def set_status(deal_id: str, status: str) -> None:
    _conn.execute("UPDATE deals SET status = ? WHERE deal_id = ?", (status, deal_id))
    _conn.commit()


def mark_released(deal_id: str) -> None:
    _conn.execute(
        "UPDATE deals SET released_at = ? WHERE deal_id = ? AND released_at IS NULL",
        (int(time.time()), deal_id),
    )
    _conn.commit()


def user_deals(user_id: int) -> list[sqlite3.Row]:
    return _conn.execute(
        "SELECT * FROM deals WHERE seller_id = ? OR buyer_id = ?", (user_id, user_id)
    ).fetchall()


def completed_worth_by_user() -> dict[int, tuple[int, float]]:
    """user_id -> (completed deal count, total worth) over completed deals."""
    totals: dict[int, list] = {}
    rows = _conn.execute(
        "SELECT seller_id, buyer_id, COALESCE(closed_amount, amount) AS amt FROM deals WHERE status = 'completed'"
    ).fetchall()
    for row in rows:
        for uid in {row["seller_id"], row["buyer_id"]}:
            if uid:
                entry = totals.setdefault(uid, [0, 0.0])
                entry[0] += 1
                entry[1] += float(row["amt"])
    return {uid: (c, w) for uid, (c, w) in totals.items()}


def close_deal(deal_id: str, amount: str, admin_id: int) -> None:
    _conn.execute(
        "UPDATE deals SET status = 'completed', closed_amount = ?, closed_by = ?, closed_at = ? "
        "WHERE deal_id = ?",
        (amount, admin_id, int(time.time()), deal_id),
    )
    _conn.commit()


def create_deal(**fields) -> str:
    with _conn:
        row = _conn.execute("SELECT MAX(num) AS m FROM deals").fetchone()
        num = max((row["m"] or 0) + 1, DEAL_ID_START)
        deal_id = f"{DEAL_ID_PREFIX}{num}"
        cols = ["num", "deal_id", "created_at", *fields]
        vals = [num, deal_id, int(time.time()), *fields.values()]
        _conn.execute(
            f"INSERT INTO deals ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals
        )
    return deal_id


def set_card_msg(deal_id: str, msg_id: int) -> None:
    _conn.execute("UPDATE deals SET card_msg_id = ? WHERE deal_id = ?", (msg_id, deal_id))
    _conn.commit()


def address_owner(address: str) -> Optional[int]:
    row = _conn.execute("SELECT admin_id FROM addresses WHERE address = ? LIMIT 1", (address,)).fetchone()
    return row["admin_id"] if row else None


def admin_addresses(admin_id: int) -> list[sqlite3.Row]:
    return _conn.execute(
        "SELECT * FROM addresses WHERE admin_id = ? ORDER BY created_at", (admin_id,)
    ).fetchall()


def address_records(address: str) -> list[sqlite3.Row]:
    return _conn.execute(
        "SELECT * FROM addresses WHERE address = ? ORDER BY created_at", (address,)
    ).fetchall()


def lock_address(address: str, display: str, network: str, token: str, admin_id: int, admin_name: str) -> None:
    _conn.execute(
        "INSERT INTO addresses (address, network, token, admin_id, admin_name, display_address, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (address, network, token, admin_id, admin_name, display, int(time.time())),
    )
    _conn.commit()


def delete_address(admin_id: int, network: str) -> bool:
    cur = _conn.execute("DELETE FROM addresses WHERE admin_id = ? AND network = ?", (admin_id, network))
    _conn.commit()
    return cur.rowcount > 0
