# database.py
# ============================================
# HABESHA BET - DATABASE LAYER
# SQLite, row_factory = sqlite3.Row throughout.
#
# Tables:
#   users            - accounts, balances, phone, language, referrals
#   transactions     - full ledger of every balance change
#   withdrawals      - pending/approved/rejected withdrawal requests
#   deposit_accounts - rotating Telebirr accounts for deposits
#   games            - one row per bingo round (per room)
#   game_players     - players in a game + their auto-win toggle
#   game_cards       - cards sold in a game (ownership + manual marks)
#   game_numbers     - the sequence of called numbers per game
#
# Concurrency notes:
#   - Card purchases use a UNIQUE index on (game_id, card_index) so two
#     players can NEVER buy the same card - the second INSERT fails with
#     IntegrityError and the whole purchase is rolled back.
#   - Transfers use a conditional UPDATE (WHERE balance >= amount) so a
#     user can never go negative even under concurrent requests.
#   - Deposit reference numbers are UNIQUE so the same Telebirr SMS can
#     never be credited twice.
# ============================================

import sqlite3
import json
import logging
import os
import shutil
from datetime import datetime, timedelta
from threading import Lock, local

import config

logger = logging.getLogger("habesha_bet")

_backup_lock = Lock()
_last_backup_ts = None
_conn_local = local()


def get_connection():
    conn = getattr(_conn_local, 'connection', None)
    if conn is not None:
        try:
            conn.execute("SELECT 1")
            return conn
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            _conn_local.connection = None
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _conn_local.connection = conn
    return conn


def init_db():
    """Create all tables and indexes if they don't exist. Safe to call every startup."""
    conn = get_connection()
    conn.execute("PRAGMA journal_mode=WAL")
    cur = conn.cursor()
    _init_tables(cur)
    conn.commit()
    init_house_wallet()


def backup_database():
    """Copy the database file to the backups directory. Safe to call
    concurrently - threads beyond the first are no-ops until the first
    backup finishes."""
    global _last_backup_ts
    with _backup_lock:
        now = datetime.utcnow()
        if _last_backup_ts and (now - _last_backup_ts).total_seconds() < 30:
            return
        try:
            src = os.path.abspath(config.DB_PATH)
            if not os.path.exists(src):
                return
            backup_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "backups"))
            os.makedirs(backup_dir, exist_ok=True)
            ts = now.strftime("%Y%m%d_%H%M%S")
            name = os.path.basename(src)
            if "." in name:
                base, ext = name.rsplit(".", 1)
                dst = os.path.join(backup_dir, f"{base}_{ts}.{ext}")
            else:
                dst = os.path.join(backup_dir, f"{name}_{ts}")
            shutil.copy2(src, dst)
            _last_backup_ts = now
            backups = sorted(
                [os.path.join(backup_dir, f) for f in os.listdir(backup_dir)],
                key=os.path.getmtime,
                reverse=True,
            )
            max_backups = getattr(config, "MAX_BACKUPS", 50)
            for old in backups[max_backups:]:
                try:
                    os.remove(old)
                except OSError:
                    pass
        except Exception:
            pass


def _init_tables(cur):

    # ---------------- USERS ----------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            phone TEXT,
            balance REAL NOT NULL DEFAULT 0,
            bonus_balance REAL NOT NULL DEFAULT 0,
            language TEXT NOT NULL DEFAULT 'am',
            referred_by INTEGER,
            referral_bonus_given INTEGER NOT NULL DEFAULT 0,
            last_bonus_claim TEXT,
            last_transfer_time TEXT,
            created_at TEXT NOT NULL,
            chat_id INTEGER
        )
    """)

    # ---- Migration for chat_id ----
    try:
        cur.execute("ALTER TABLE users ADD COLUMN chat_id INTEGER")
    except sqlite3.OperationalError:
        pass

    # ---- Migration for bonus_balance ----
    try:
        cur.execute("ALTER TABLE users ADD COLUMN bonus_balance REAL NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    # ---- Migration for language ----
    try:
        cur.execute("ALTER TABLE users ADD COLUMN language TEXT NOT NULL DEFAULT 'am'")
    except sqlite3.OperationalError:
        pass

    # ---- Migration for referred_by ----
    try:
        cur.execute("ALTER TABLE users ADD COLUMN referred_by INTEGER")
    except sqlite3.OperationalError:
        pass

    # ---- Migration for referral_bonus_given ----
    try:
        cur.execute("ALTER TABLE users ADD COLUMN referral_bonus_given INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    # ---- Migration for last_bonus_claim ----
    try:
        cur.execute("ALTER TABLE users ADD COLUMN last_bonus_claim TEXT")
    except sqlite3.OperationalError:
        pass

    # ---- Migration for last_transfer_time ----
    try:
        cur.execute("ALTER TABLE users ADD COLUMN last_transfer_time TEXT")
    except sqlite3.OperationalError:
        pass

    # ---------------- TRANSACTIONS (full ledger) ----------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            type TEXT NOT NULL,
            -- types: deposit, withdraw, withdraw_refund, transfer_in, transfer_out,
            --        bingo_bet, bingo_win, bingo_refund,
            --        referral_bonus, signup_bonus, daily_bonus,
            --        house_commission
            amount REAL NOT NULL,
            reference TEXT,
            status TEXT NOT NULL,   -- completed, pending, rejected
            created_at TEXT NOT NULL,
            receipt_no TEXT,
            verification_status TEXT,   -- pending, verified, failed
            verification_raw TEXT
        )
    """)
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_reference
        ON transactions(reference)
        WHERE reference IS NOT NULL
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tx_user ON transactions(user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tx_type ON transactions(type)")

    # ---------------- WITHDRAWALS ----------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            phone TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL
        )
    """)

    # ---------------- ADMIN AUDIT LOG ----------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS admin_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            target_id INTEGER,
            details TEXT,
            created_at TEXT NOT NULL
        )
    """)

    # ---------------- DEPOSIT ACCOUNTS (rotating Telebirr numbers) ----------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS deposit_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT NOT NULL,
            recipient_name TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 0,
            deposit_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)

    # ---------------- GAMES ----------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_fee REAL NOT NULL,
            state TEXT NOT NULL DEFAULT 'waiting',  -- waiting, running, finished
            pool REAL NOT NULL DEFAULT 0,
            house_cut REAL,
            winner_ids TEXT,           -- JSON list of user_ids, set when finished
            winner_cards TEXT,          -- JSON map user_id -> [winning card_index,...]
            per_winner_amount REAL,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            countdown_started_at TEXT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_games_room_state ON games(room_fee, state)")
    try:
        cur.execute("ALTER TABLE games ADD COLUMN countdown_started_at TEXT")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE games ADD COLUMN winner_cards TEXT")
    except Exception:
        pass

    # ---------------- MANUAL BINGO CLAIMS ----------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS manual_bingo_claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            card_indices TEXT NOT NULL,
            created_at TEXT NOT NULL,
            resolved INTEGER NOT NULL DEFAULT 0
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_manual_claims_game ON manual_bingo_claims(game_id, resolved)")

    # ---------------- GAME PLAYERS ----------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS game_players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            cards_count INTEGER NOT NULL DEFAULT 0,
            auto_win INTEGER NOT NULL DEFAULT 0,
            chat_id INTEGER,
            message_id INTEGER,
            created_at TEXT NOT NULL,
            UNIQUE(game_id, user_id)
        )
    """)

    # ---------------- GAME CARDS ----------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS game_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id INTEGER NOT NULL,
            card_index INTEGER NOT NULL,   -- 0-199, position in the 200-card pool
            owner_id INTEGER NOT NULL,
            marked_numbers TEXT NOT NULL DEFAULT '[]',  -- JSON list, manual tap-to-highlight
            created_at TEXT NOT NULL,
            UNIQUE(game_id, card_index)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cards_game ON game_cards(game_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cards_owner ON game_cards(game_id, owner_id)")

    # ---------------- GAME NUMBERS (call sequence) ----------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS game_numbers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id INTEGER NOT NULL,
            call_order INTEGER NOT NULL,   -- 1, 2, 3 ... up to 75
            number INTEGER NOT NULL,       -- the ball number 1-75
            called_at TEXT NOT NULL,
            UNIQUE(game_id, call_order),
            UNIQUE(game_id, number)
        )
    """)

    # ---------------- JACKPOT ----------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS jackpot (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            current_amount REAL NOT NULL DEFAULT 0,
            room_fee INTEGER NOT NULL,
            triggered INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )
    """)

    # ---- Migration for older DBs (safe to run every time) ----
    for column, col_def in [
        ("phone", "TEXT"),
        ("language", "TEXT NOT NULL DEFAULT 'am'"),
        ("referred_by", "INTEGER"),
        ("referral_bonus_given", "INTEGER NOT NULL DEFAULT 0"),
        ("last_bonus_claim", "TEXT"),
        ("last_transfer_time", "TEXT"),
        ("bonus_balance", "REAL NOT NULL DEFAULT 0"),
        ("daily_streak", "INTEGER NOT NULL DEFAULT 0"),
        ("last_bonus_claim_date", "TEXT"),
        ("chat_id", "INTEGER"),
    ]:
        try:
            cur.execute(f"ALTER TABLE users ADD COLUMN {column} {col_def}")
        except sqlite3.OperationalError:
            pass

    # ---- Migration for chat_id ----
    try:
        cur.execute("ALTER TABLE users ADD COLUMN chat_id INTEGER")
    except sqlite3.OperationalError:
        pass

    # ---- Migration for receipt verification columns on transactions ----
    for column, col_def in [
        ("receipt_no", "TEXT"),
        ("verification_status", "TEXT"),
        ("verification_raw", "TEXT"),
    ]:
        try:
            cur.execute(f"ALTER TABLE transactions ADD COLUMN {column} {col_def}")
        except sqlite3.OperationalError:
            pass


# =====================================================================
# USERS
# =====================================================================

def find_user_by_username(username: str) -> sqlite3.Row:
    """Case-insensitive lookup by username (without leading @), used for
    the transfer flow where the sender types the recipient's @handle.
    Returns the most recently created matching user if somehow more than
    one row shares a username (e.g. stale data from a username change)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM users WHERE LOWER(username) = LOWER(?) ORDER BY created_at DESC LIMIT 1",
        (username,)
    )
    row = cur.fetchone()
    return row


def get_or_create_user(user_id: int, username: str, referred_by: int = None) -> sqlite3.Row:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user = cur.fetchone()

    if user is None:
        cur.execute(
            "INSERT INTO users (user_id, username, balance, language, referred_by, created_at) "
            "VALUES (?, ?, 0, ?, ?, ?)",
            (user_id, username, config.DEFAULT_LANGUAGE, referred_by, datetime.utcnow().isoformat())
        )
        conn.commit()
        cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = cur.fetchone()
    return user


def get_user(user_id: int) -> sqlite3.Row:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user = cur.fetchone()
    return user


def get_balance(user_id: int) -> float:
    user = get_user(user_id)
    return user["balance"] if user else 0.0


def get_bonus_balance(user_id: int) -> float:
    user = get_user(user_id)
    if not user:
        return 0.0
    try:
        return user["bonus_balance"]
    except (KeyError, IndexError):
        return 0.0


def adjust_balance(user_id: int, amount: float) -> float:
    """Add (or subtract, if negative) to a user's balance. Returns new balance."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    cur.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    new_balance = cur.fetchone()["balance"]
    return new_balance


def add_bonus_balance(user_id: int, amount: float):
    """Add to a user's lifetime bonus balance (referrals, daily/signup
    bonuses). Kept separate from `balance` so the UI can show how much a
    player has earned from bonuses vs. deposited/won funds."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET bonus_balance = bonus_balance + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()


def subtract_bonus_balance(user_id: int, amount: float) -> bool:
    """Atomically deduct from bonus_balance. Returns True if successful."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET bonus_balance = bonus_balance - ? WHERE user_id = ? AND bonus_balance >= ?",
        (amount, user_id, amount)
    )
    success = cur.rowcount > 0
    if success:
        conn.commit()
    return success


def spend_funds(user_id: int, amount: float, conn=None, cur=None) -> tuple:
    """Spend bonus_balance first, then balance. Atomic within caller
    transaction when conn/cur are provided. Returns (success, reason)."""
    owns_conn = conn is None
    if owns_conn:
        conn = get_connection()
        cur = conn.cursor()

    cur.execute(
        "UPDATE users SET bonus_balance = bonus_balance - ? WHERE user_id = ? AND bonus_balance >= ?",
        (amount, user_id, amount)
    )
    if cur.rowcount > 0:
        if owns_conn:
            conn.commit()
        return True, "ok"

    cur.execute(
        "UPDATE users SET balance = balance - ? WHERE user_id = ? AND balance >= ?",
        (amount, user_id, amount)
    )
    if cur.rowcount > 0:
        if owns_conn:
            conn.commit()
            conn.close()
        return True, "ok"

    if owns_conn:
        conn.close()
    return False, "insufficient_funds"


def set_user_phone(user_id: int, phone: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET phone = ? WHERE user_id = ?", (phone, user_id))
    conn.commit()


def set_user_language(user_id: int, lang: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET language = ? WHERE user_id = ?", (lang, user_id))
    conn.commit()


def update_user_chat_id(user_id: int, chat_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET chat_id = ? WHERE user_id = ?", (chat_id, user_id))
    conn.commit()


def get_all_user_ids() -> list:
    """For broadcast - returns all registered user_ids."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users")
    rows = cur.fetchall()
    return [r["user_id"] for r in rows]


def count_users() -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as c FROM users")
    row = cur.fetchone()
    return row["c"]


# =====================================================================
# TRANSACTIONS / LEDGER
# =====================================================================

def record_transaction(user_id: int, tx_type: str, amount: float, reference: str = None, status: str = "completed", receipt_no: str = None, verification_status: str = None, verification_raw: str = None):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO transactions (user_id, type, amount, reference, status, created_at, receipt_no, verification_status, verification_raw) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, tx_type, amount, reference, status, datetime.utcnow().isoformat(), receipt_no, verification_status, verification_raw)
    )
    conn.commit()


def reference_already_used(reference: str) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM transactions WHERE reference = ?", (reference,))
    row = cur.fetchone()
    return row is not None


def get_user_transactions(user_id: int, limit: int = 10) -> list:
    """Most recent transactions for this user, newest first - used for
    the '/Transactions' menu screen."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM transactions WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
        (user_id, limit)
    )
    rows = cur.fetchall()
    return rows


def count_deposits(user_id: int) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) as c FROM transactions WHERE user_id = ? AND type = 'deposit' AND status = 'completed'",
        (user_id,)
    )
    row = cur.fetchone()
    return row["c"] if row else 0


def get_total_collected() -> float:
    """Sum of all completed deposits - for admin dashboard."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(SUM(amount),0) as total FROM transactions WHERE type='deposit' AND status='completed'")
    row = cur.fetchone()
    return row["total"]


def get_net_profit() -> float:
    """Sum of all house_commission transactions - for admin dashboard."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(SUM(amount),0) as total FROM transactions WHERE type='house_commission'")
    row = cur.fetchone()
    return row["total"]


def get_peak_hours() -> list:
    """Returns [(hour_0_23, count), ...] based on bingo_bet transactions (UTC hour)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT CAST(strftime('%H', created_at) AS INTEGER) as hour, COUNT(*) as count
        FROM transactions
        WHERE type = 'bingo_bet'
        GROUP BY hour
        ORDER BY hour
    """)
    rows = cur.fetchall()
    return [(r["hour"], r["count"]) for r in rows]


# =====================================================================
# REFERRALS & BONUSES
# =====================================================================

def count_referrals(user_id: int) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as c FROM users WHERE referred_by = ?", (user_id,))
    row = cur.fetchone()
    return row["c"] if row else 0


def mark_referral_bonus_given(user_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET referral_bonus_given = 1 WHERE user_id = ?", (user_id,))
    conn.commit()


def can_claim_daily_streak_bonus(user_id: int):
    """Returns (can_claim: bool, streak_days: int, next_bonus_amount: float)."""
    user = get_user(user_id)
    today = datetime.utcnow().date().isoformat()

    if user is None or user["last_bonus_claim_date"] is None:
        return True, 1, config.DAILY_STREAK_BONUSES[1]

    last_date = user["last_bonus_claim_date"]
    last = datetime.fromisoformat(last_date).date()
    now = datetime.utcnow().date()
    delta = (now - last).days

    if delta == 0:
        return False, user["daily_streak"], 0.0

    current_streak = user["daily_streak"]
    if delta == 1:
        current_streak += 1
    else:
        current_streak = 1

    bonus = config.DAILY_STREAK_BONUSES.get(current_streak, config.DAILY_BONUS_AMOUNT)
    return True, current_streak, bonus


def set_daily_streak_bonus_claimed(user_id: int, streak: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET last_bonus_claim_date = ?, daily_streak = ? WHERE user_id = ?",
        (datetime.utcnow().date().isoformat(), streak, user_id),
    )
    conn.commit()


def get_daily_streak(user_id: int) -> int:
    user = get_user(user_id)
    if user is None:
        return 0
    return user["daily_streak"]


# =====================================================================
# TRANSFERS (user to user, atomic with cooldown)
# =====================================================================

def can_transfer(user_id: int):
    """Returns (can_transfer: bool, seconds_remaining: int)."""
    user = get_user(user_id)
    if user is None or user["last_transfer_time"] is None:
        return True, 0

    last = datetime.fromisoformat(user["last_transfer_time"])
    cooldown = timedelta(seconds=config.TRANSFER_COOLDOWN_SECONDS)
    elapsed = datetime.utcnow() - last

    if elapsed >= cooldown:
        return True, 0

    remaining = cooldown - elapsed
    return False, int(remaining.total_seconds())


def transfer_funds(from_id: int, to_id: int, amount: float):
    """Atomically move funds between two users.
    Returns (success: bool, reason: str)."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("BEGIN IMMEDIATE")

        # Conditional debit - bonus first, prevents negative balances even under concurrent requests.
        success, reason = spend_funds(from_id, amount, conn=conn, cur=cur)
        if not success:
            conn.rollback()
            return False, reason

        cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, to_id))
        cur.execute(
            "UPDATE users SET last_transfer_time = ? WHERE user_id = ?",
            (datetime.utcnow().isoformat(), from_id)
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    record_transaction(from_id, "transfer_out", -amount, status="completed")
    record_transaction(to_id, "transfer_in", amount, status="completed")
    return True, "ok"


# =====================================================================
# WITHDRAWALS
# =====================================================================

def create_withdrawal(user_id: int, amount: float, phone: str) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO withdrawals (user_id, amount, phone, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
        (user_id, amount, phone, datetime.utcnow().isoformat())
    )
    conn.commit()
    withdrawal_id = cur.lastrowid
    return withdrawal_id


def get_withdrawal(withdrawal_id: int) -> sqlite3.Row:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM withdrawals WHERE id = ?", (withdrawal_id,))
    row = cur.fetchone()
    return row


def get_pending_withdrawals() -> list:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM withdrawals WHERE status = 'pending' ORDER BY created_at ASC")
    rows = cur.fetchall()
    return rows


def update_withdrawal_status(withdrawal_id: int, status: str):
    """Update status only if currently pending. Returns True if updated."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE withdrawals SET status = ? WHERE id = ? AND status = 'pending'", (status, withdrawal_id))
    success = cur.rowcount > 0
    conn.commit()
    return success


# =====================================================================
# DEPOSIT ACCOUNTS (rotating Telebirr numbers)
# =====================================================================

def add_deposit_account(phone: str, recipient_name: str) -> int:
    """Add a new deposit account. If it's the first account, make it active."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as c FROM deposit_accounts")
    is_first = cur.fetchone()["c"] == 0

    cur.execute(
        "INSERT INTO deposit_accounts (phone, recipient_name, active, deposit_count, created_at) VALUES (?, ?, ?, 0, ?)",
        (phone, recipient_name, 1 if is_first else 0, datetime.utcnow().isoformat())
    )
    conn.commit()
    account_id = cur.lastrowid
    return account_id


def remove_deposit_account(account_id: int):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT active FROM deposit_accounts WHERE id = ?", (account_id,))
    row = cur.fetchone()
    was_active = row["active"] if row else 0

    cur.execute("DELETE FROM deposit_accounts WHERE id = ?", (account_id,))

    if was_active:
        # Promote another account to active, if any remain
        cur.execute("SELECT id FROM deposit_accounts ORDER BY id LIMIT 1")
        next_row = cur.fetchone()
        if next_row:
            cur.execute("UPDATE deposit_accounts SET active = 1 WHERE id = ?", (next_row["id"],))

    conn.commit()


def list_deposit_accounts() -> list:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM deposit_accounts ORDER BY id")
    rows = cur.fetchall()
    return rows


def get_active_deposit_accounts() -> list:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM deposit_accounts WHERE active = 1 ORDER BY id")
    rows = cur.fetchall()
    return rows


def get_active_deposit_account() -> sqlite3.Row:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM deposit_accounts WHERE active = 1 LIMIT 1")
    row = cur.fetchone()
    return row


def record_deposit_for_account(account_id: int):
    """Increment the active account's deposit counter. If it reaches the
    rotation threshold, switch the active flag to the next account
    (round-robin by id) and reset this account's counter."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("UPDATE deposit_accounts SET deposit_count = deposit_count + 1 WHERE id = ?", (account_id,))
    cur.execute("SELECT deposit_count FROM deposit_accounts WHERE id = ?", (account_id,))
    row = cur.fetchone()

    if row and row["deposit_count"] >= config.ROTATE_AFTER_DEPOSITS:
        cur.execute("SELECT id FROM deposit_accounts ORDER BY id")
        all_ids = [r["id"] for r in cur.fetchall()]

        if len(all_ids) > 1:
            current_index = all_ids.index(account_id)
            next_id = all_ids[(current_index + 1) % len(all_ids)]

            cur.execute("UPDATE deposit_accounts SET active = 0 WHERE id = ?", (account_id,))
            cur.execute("UPDATE deposit_accounts SET active = 1, deposit_count = 0 WHERE id = ?", (next_id,))
        else:
            # Only one account - just reset its counter
            cur.execute("UPDATE deposit_accounts SET deposit_count = 0 WHERE id = ?", (account_id,))

    conn.commit()


# =====================================================================
# GAMES
# =====================================================================

def is_game_stuck(game_id: int) -> tuple:
    """Return (stuck, reason_string) if a game has been in waiting/running
    longer than the maximum possible round duration."""
    game = get_game(game_id)
    if not game or game["state"] not in ("waiting", "running"):
        return False, "not_active"

    now = datetime.utcnow()

    if game["state"] == "waiting":
        if not game["countdown_started_at"]:
            return False, "no_countdown"
        ts = datetime.fromisoformat(game["countdown_started_at"])
        max_duration = config.COUNTDOWN_SECONDS + 60
    else:
        if game["countdown_started_at"]:
            ts = datetime.fromisoformat(game["countdown_started_at"])
            max_duration = (
                config.COUNTDOWN_SECONDS
                + config.MAX_NUMBERS_CALLED * config.CALL_DELAY_SECONDS
                + 60
            )
        elif game["started_at"]:
            ts = datetime.fromisoformat(game["started_at"])
            max_duration = config.MAX_NUMBERS_CALLED * config.CALL_DELAY_SECONDS + 60
        else:
            return False, "no_timestamp"

    try:
        elapsed = (now - ts).total_seconds()
        if elapsed > max_duration:
            return True, f"elapsed={int(elapsed)}s max={max_duration}s state={game['state']}"
    except Exception:
        pass
    return False, ""


def get_or_create_active_game(room_fee: float) -> sqlite3.Row:
    """Get the current waiting/running game for this room fee,
    or create a fresh 'waiting' game if none exists."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM games WHERE room_fee = ? AND state IN ('waiting','running') ORDER BY id DESC LIMIT 1",
        (room_fee,)
    )
    game = cur.fetchone()

    if game is None:
        cur.execute(
            "INSERT INTO games (room_fee, state, pool, created_at) VALUES (?, 'waiting', 0, ?)",
            (room_fee, datetime.utcnow().isoformat())
        )
        conn.commit()
        cur.execute("SELECT * FROM games WHERE id = ?", (cur.lastrowid,))
        game = cur.fetchone()
    return game


def get_game(game_id: int) -> sqlite3.Row:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM games WHERE id = ?", (game_id,))
    row = cur.fetchone()
    return row


def set_game_state(game_id: int, state: str):
    conn = get_connection()
    cur = conn.cursor()
    if state == "running":
        cur.execute(
            "UPDATE games SET state = ?, started_at = ? WHERE id = ?",
            (state, datetime.utcnow().isoformat(), game_id)
        )
    else:
        cur.execute("UPDATE games SET state = ? WHERE id = ?", (state, game_id))
    conn.commit()


def finish_game(game_id: int, winner_ids: list, house_cut: float, per_winner_amount: float, winner_cards: dict = None):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE games SET state = 'finished', winner_ids = ?, winner_cards = ?, house_cut = ?, "
        "per_winner_amount = ?, finished_at = ? WHERE id = ?",
        (json.dumps(winner_ids), json.dumps(winner_cards or {}), house_cut, per_winner_amount, datetime.utcnow().isoformat(), game_id)
    )
    conn.commit()


def set_game_countdown_start(game_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE games SET countdown_started_at = ? WHERE id = ?",
        (datetime.utcnow().isoformat(), game_id)
    )
    conn.commit()


def clear_game_countdown_start(game_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE games SET countdown_started_at = NULL WHERE id = ?",
        (game_id,)
    )
    conn.commit()


def get_pool(game_id: int) -> float:
    game = get_game(game_id)
    return game["pool"] if game else 0.0


def get_prize_pool(game_id: int) -> float:
    game = get_game(game_id)
    if not game:
        return 0.0
    pool = game["pool"]
    house_cut = round(pool * config.HOUSE_COMMISSION_PERCENT / 100, 2)
    return round(pool - house_cut, 2)


# =====================================================================
# GAME PLAYERS
# =====================================================================

def upsert_game_player_message(game_id: int, user_id: int, chat_id: int, message_id: int):
    """Store/update where this player's live game message lives, so the
    number-calling loop can edit it directly."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM game_players WHERE game_id = ? AND user_id = ?", (game_id, user_id))
    row = cur.fetchone()

    if row:
        cur.execute(
            "UPDATE game_players SET chat_id = ?, message_id = ? WHERE game_id = ? AND user_id = ?",
            (chat_id, message_id, game_id, user_id)
        )
    else:
        cur.execute(
            "INSERT INTO game_players (game_id, user_id, cards_count, auto_win, chat_id, message_id, created_at) "
            "VALUES (?, ?, 0, 0, ?, ?, ?)",
            (game_id, user_id, chat_id, message_id, datetime.utcnow().isoformat())
        )
    conn.commit()


def get_game_player(game_id: int, user_id: int) -> sqlite3.Row:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM game_players WHERE game_id = ? AND user_id = ?", (game_id, user_id))
    row = cur.fetchone()
    return row


def get_game_players(game_id: int) -> list:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM game_players WHERE game_id = ? ORDER BY id ASC", (game_id,))
    rows = cur.fetchall()
    return rows


def get_user_chat_id(user_id: int) -> int | None:
    user = get_user(user_id)
    if user and user.get("chat_id"):
        return user["chat_id"]
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT chat_id FROM game_players WHERE user_id = ? AND chat_id IS NOT NULL ORDER BY id DESC LIMIT 1", (user_id,))
    row = cur.fetchone()
    return row["chat_id"] if row else None


def set_auto_win(game_id: int, user_id: int, value: bool):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE game_players SET auto_win = ? WHERE game_id = ? AND user_id = ?",
        (1 if value else 0, game_id, user_id)
    )
    conn.commit()


# =====================================================================
# GAME CARDS
# =====================================================================

def get_taken_cards(game_id: int) -> set:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT card_index FROM game_cards WHERE game_id = ?", (game_id,))
    rows = cur.fetchall()
    return {r["card_index"] for r in rows}


def get_player_cards(game_id: int, user_id: int) -> list:
    """Returns list of card_index values owned by this user in this game, ordered."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT card_index FROM game_cards WHERE game_id = ? AND owner_id = ? ORDER BY card_index ASC",
        (game_id, user_id)
    )
    rows = cur.fetchall()
    return [r["card_index"] for r in rows]


def get_user_active_game(user_id: int) -> sqlite3.Row:
    """Return the most recent game the user owns cards in that is still
    joinable (waiting or running), or None if they have no live game to
    resume. Used to surface an 'Open game' / rejoin option when a player
    re-opens the Mini App after closing it mid-round."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT g.* FROM games g
        JOIN game_cards c ON c.game_id = g.id
        WHERE c.owner_id = ?
          AND g.state IN ('waiting','running')
        GROUP BY g.id
        ORDER BY g.id DESC
        LIMIT 1
        """,
        (user_id,)
    )
    row = cur.fetchone()
    return row


def count_cards_sold(game_id: int) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as c FROM game_cards WHERE game_id = ?", (game_id,))
    row = cur.fetchone()
    return row["c"]


def get_all_game_cards(game_id: int) -> list:
    """Returns all cards in a game with owner info - used for refunds / payouts."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM game_cards WHERE game_id = ?", (game_id,))
    rows = cur.fetchall()
    return rows


def get_games_player_counts(game_ids: list) -> dict:
    """Return {game_id: player_count} for all given game_ids in one query."""
    if not game_ids:
        return {}
    conn = get_connection()
    cur = conn.cursor()
    placeholders = ",".join("?" for _ in game_ids)
    cur.execute(f"SELECT game_id, COUNT(*) as c FROM game_players WHERE game_id IN ({placeholders}) GROUP BY game_id", game_ids)
    rows = cur.fetchall()
    return {r["game_id"]: r["c"] for r in rows}


def get_games_cards_sold(game_ids: list) -> dict:
    """Return {game_id: cards_sold} for all given game_ids in one query."""
    if not game_ids:
        return {}
    conn = get_connection()
    cur = conn.cursor()
    placeholders = ",".join("?" for _ in game_ids)
    cur.execute(f"SELECT game_id, COUNT(*) as c FROM game_cards WHERE game_id IN ({placeholders}) GROUP BY game_id", game_ids)
    rows = cur.fetchall()
    return {r["game_id"]: r["c"] for r in rows}


def update_marked_numbers(game_id: int, card_index: int, marked_list: list):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE game_cards SET marked_numbers = ? WHERE game_id = ? AND card_index = ?",
        (json.dumps(marked_list), game_id, card_index)
    )
    conn.commit()


def get_marked_numbers(game_id: int, card_index: int) -> list:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT marked_numbers FROM game_cards WHERE game_id = ? AND card_index = ?",
        (game_id, card_index)
    )
    row = cur.fetchone()
    if row is None:
        return []
    return json.loads(row["marked_numbers"])


def purchase_cards(game_id: int, user_id: int, card_indices: list, fee_per_card: float):
    """Atomically purchase one or more cards for a game.

    Validates:
      - sufficient balance for total cost
      - none of the requested cards are already taken (UNIQUE constraint)
      - player's total cards in this game won't exceed MAX_CARDS_PER_PLAYER

    On success: deducts balance, adds to the game pool, records ownership,
    and updates/creates the game_players row.

    Returns (success: bool, reason: str)
      reason in {"ok", "insufficient_balance", "card_taken", "max_cards_exceeded"}
    """
    total_cost = fee_per_card * len(card_indices)
    now = datetime.utcnow().isoformat()

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("BEGIN IMMEDIATE")

        # --- Check max cards per player ---
        cur.execute("SELECT cards_count FROM game_players WHERE game_id = ? AND user_id = ?", (game_id, user_id))
        gp = cur.fetchone()
        existing_count = gp["cards_count"] if gp else 0

        if existing_count + len(card_indices) > config.MAX_CARDS_PER_PLAYER:
            conn.rollback()
            return False, "max_cards_exceeded"

        # --- Conditional balance debit (atomic, bonus first, prevents negative balance) ---
        success, reason = spend_funds(user_id, total_cost, conn=conn, cur=cur)
        if not success:
            conn.rollback()
            return False, reason

        # --- Insert cards (UNIQUE constraint prevents double-selling) ---
        for card_index in card_indices:
            cur.execute(
                "INSERT INTO game_cards (game_id, card_index, owner_id, marked_numbers, created_at) "
                "VALUES (?, ?, ?, '[]', ?)",
                (game_id, card_index, user_id, now)
            )

        # --- Update pool ---
        cur.execute("UPDATE games SET pool = pool + ? WHERE id = ?", (total_cost, game_id))

        # --- Upsert game_players ---
        if gp is None:
            cur.execute(
                "INSERT INTO game_players (game_id, user_id, cards_count, auto_win, created_at) "
                "VALUES (?, ?, ?, 0, ?)",
                (game_id, user_id, len(card_indices), now)
            )
        else:
            cur.execute(
                "UPDATE game_players SET cards_count = cards_count + ? WHERE game_id = ? AND user_id = ?",
                (len(card_indices), game_id, user_id)
            )

        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        return False, "card_taken"
    except Exception:
        conn.rollback()
        raise
    record_transaction(user_id, "bingo_bet", -total_cost, status="completed")
    return True, "ok"


def refund_game(game_id: int):
    """Refund every player for every card they bought in this game.
    Used when <2 cards sold at countdown end, or no winner after 75 calls."""
    cards = get_all_game_cards(game_id)
    game = get_game(game_id)
    fee = game["room_fee"]

    refunded = {}
    for card in cards:
        owner = card["owner_id"]
        refunded[owner] = refunded.get(owner, 0) + fee

    for user_id, amount in refunded.items():
        adjust_balance(user_id, amount)
        record_transaction(user_id, "bingo_refund", amount, status="completed")

    return refunded  # {user_id: amount_refunded}


# =====================================================================
# GAME NUMBERS (call sequence)
# =====================================================================

def add_called_number(game_id: int, call_order: int, number: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO game_numbers (game_id, call_order, number, called_at) VALUES (?, ?, ?, ?)",
        (game_id, call_order, number, datetime.utcnow().isoformat())
    )
    conn.commit()


def get_called_numbers(game_id: int) -> list:
    """Returns the called numbers in call order."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT number FROM game_numbers WHERE game_id = ? ORDER BY call_order ASC", (game_id,))
    rows = cur.fetchall()
    return [r["number"] for r in rows]


def get_call_count(game_id: int) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as c FROM game_numbers WHERE game_id = ?", (game_id,))
    row = cur.fetchone()
    return row["c"]


# =====================================================================
# ADMIN AUDIT LOG
# =====================================================================

def record_admin_action(admin_id: int, action: str, target_id=None, details=None):
    if details is not None and not isinstance(details, str):
        details = json.dumps(details)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO admin_audit_log (admin_id, action, target_id, details, created_at) VALUES (?, ?, ?, ?, ?)",
        (admin_id, action, target_id, details, datetime.utcnow().isoformat())
    )
    conn.commit()


def get_total_games_played() -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as c FROM games WHERE state = 'finished'")
    row = cur.fetchone()
    return row["c"]


def get_total_unique_players() -> int:
    """Number of distinct users who have ever bought a bingo card."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(DISTINCT owner_id) as c FROM game_cards")
    row = cur.fetchone()
    return row["c"]


# =====================================================================
# HOUSE WALLET
# Single-row table tracking cumulative house commission.
# Admin can view and withdraw from it via /admin panel.
# =====================================================================

def init_house_wallet():
    """Ensure house_wallet table and its single row exist.
    Called inside init_db() automatically."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS house_wallet (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            balance REAL NOT NULL DEFAULT 0,
            total_earned REAL NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )
    """)
    cur.execute("""
        INSERT OR IGNORE INTO house_wallet (id, balance, total_earned, updated_at)
        VALUES (1, 0, 0, ?)
    """, (datetime.utcnow().isoformat(),))

    cur.execute("""
        INSERT OR IGNORE INTO jackpot (id, current_amount, room_fee, triggered, updated_at)
        VALUES (1, 0, ?, 0, ?)
    """, (config.JACKPOT_ROOM_FEE, datetime.utcnow().isoformat()))
    conn.commit()


def get_house_balance() -> float:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT balance FROM house_wallet WHERE id = 1")
    row = cur.fetchone()
    return row["balance"] if row else 0.0


def get_house_total_earned() -> float:
    """Cumulative all-time commission - never decreases."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT total_earned FROM house_wallet WHERE id = 1")
    row = cur.fetchone()
    return row["total_earned"] if row else 0.0


def add_house_commission(amount: float) -> float:
    """Credit the house wallet. Returns new balance."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE house_wallet
        SET balance = balance + ?,
            total_earned = total_earned + ?,
            updated_at = ?
        WHERE id = 1
    """, (amount, amount, datetime.utcnow().isoformat()))

    jackpot_contrib = round(amount * config.JACKPOT_CONTRIBUTION_PERCENT / 100, 2)
    if jackpot_contrib > 0:
        cur.execute(
            "UPDATE jackpot SET current_amount = current_amount + ?, updated_at = ? WHERE id = 1",
            (jackpot_contrib, datetime.utcnow().isoformat()),
        )

    conn.commit()
    cur.execute("SELECT balance FROM house_wallet WHERE id = 1")
    new_balance = cur.fetchone()["balance"]
    return new_balance


def credit_house(amount: float) -> float:
    """Credit the house wallet AND record a house_commission ledger entry.
    Call once per finished game with that game's house cut.
    Returns the new house wallet balance."""
    new_balance = add_house_commission(amount)
    record_transaction(config.HOUSE_ACCOUNT_ID, "house_commission", amount, status="completed")
    return new_balance


def get_jackpot() -> dict:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM jackpot WHERE id = 1")
    row = cur.fetchone()
    if row is None:
        return {
            "id": 1,
            "current_amount": 0.0,
            "room_fee": config.JACKPOT_ROOM_FEE,
            "triggered": 0,
            "updated_at": datetime.utcnow().isoformat(),
        }
    return dict(row)


def reset_jackpot():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE jackpot SET current_amount = 0, triggered = 0, updated_at = ? WHERE id = 1",
        (datetime.utcnow().isoformat(),),
    )
    conn.commit()


def trigger_jackpot(game_id: int) -> float:
    """Add JACKPOT_TRIGGER_AMOUNT to the game's pool and reset jackpot.
    Returns the bonus amount added."""
    conn = get_connection()
    cur = conn.cursor()
    bonus = float(config.JACKPOT_TRIGGER_AMOUNT)
    cur.execute("UPDATE games SET pool = pool + ? WHERE id = ?", (bonus, game_id))
    cur.execute(
        "UPDATE jackpot SET current_amount = 0, triggered = 1, updated_at = ? WHERE id = 1",
        (datetime.utcnow().isoformat(),),
    )
    conn.commit()
    return bonus


def withdraw_house_funds(amount: float):
    """Deduct from house wallet. Returns (success, reason, new_balance)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("BEGIN IMMEDIATE")
    cur.execute("SELECT balance FROM house_wallet WHERE id = 1")
    row = cur.fetchone()
    if row is None or row["balance"] < amount:
        conn.rollback()
        return False, "insufficient_house_balance", 0.0

    cur.execute("""
        UPDATE house_wallet SET balance = balance - ?, updated_at = ?
        WHERE id = 1
    """, (amount, datetime.utcnow().isoformat()))
    conn.commit()
    cur.execute("SELECT balance FROM house_wallet WHERE id = 1")
    new_bal = cur.fetchone()["balance"]
    return True, "ok", new_bal


def record_manual_bingo_claim(game_id: int, user_id: int, card_indices: list):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO manual_bingo_claims (game_id, user_id, card_indices, created_at) VALUES (?, ?, ?, ?)",
        (game_id, user_id, json.dumps(card_indices), datetime.utcnow().isoformat())
    )
    conn.commit()


def get_manual_bingo_claims(game_id: int) -> dict:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT user_id, card_indices FROM manual_bingo_claims WHERE game_id = ? AND resolved = 0",
        (game_id,)
    )
    rows = cur.fetchall()
    return {row["user_id"]: json.loads(row["card_indices"]) for row in rows}


def clear_manual_bingo_claims(game_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM manual_bingo_claims WHERE game_id = ?", (game_id,))
    conn.commit()


def force_finish_stuck_game(game_id: int) -> tuple:
    """Admin helper: force-finish a stuck game and refund all players."""
    game = get_game(game_id)
    if game is None or game["state"] == "finished":
        return False, "game_not_found_or_finished"
    try:
        refund_game(game_id)
        clear_manual_bingo_claims(game_id)
        set_game_state(game_id, "finished")
        logger.info("[force-finish] game %s force-finished", game_id)
        return True, "ok"
    except Exception:
        logger.exception("[force-finish] failed for game %s", game_id)
        return False, "db_error"

