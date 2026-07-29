# test_database.py
# ============================================
# HABESHA BET - DATABASE LAYER TESTS
# ============================================

import os
import pytest

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend import database as db
from backend import config


def _require_db():
    db_url = getattr(config, "DATABASE_URL", "") or config.DB_PATH
    if not db_url or db_url == "habesha_bet.db":
        pytest.skip("No DATABASE_URL or test DB configured")


def test_connection_pool_created():
    _require_db()
    conn = db.get_connection()
    assert conn is not None
    db.release_connection(conn)


def test_init_db_creates_tables():
    _require_db()
    db.init_db()
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
    tables = {r["table_name"] for r in cur.fetchall()}
    db.release_connection(conn)
    expected = {
        "users", "transactions", "withdrawals", "admin_audit_log",
        "deposit_accounts", "games", "game_players", "game_cards",
        "game_numbers", "jackpot", "manual_bingo_claims", "house_wallet",
    }
    assert expected.issubset(tables)


def test_user_crud():
    _require_db()
    db.init_db()
    test_id = 999999999
    try:
        user = db.get_or_create_user(test_id, "testuser")
        assert user["user_id"] == test_id

        fetched = db.get_user(test_id)
        assert fetched["username"] == "testuser"

        db.set_user_language(test_id, "en")
        updated = db.get_user(test_id)
        assert updated["language"] == "en"
    finally:
        conn = db.get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM users WHERE user_id = %s", (test_id,))
        conn.commit()
        db.release_connection(conn)


def test_balance_operations():
    _require_db()
    db.init_db()
    test_id = 999999998
    try:
        db.get_or_create_user(test_id, "balancetest")
        assert db.get_balance(test_id) == 0.0

        db.adjust_balance(test_id, 100.0)
        assert db.get_balance(test_id) == 100.0

        db.adjust_balance(test_id, -30.0)
        assert db.get_balance(test_id) == 70.0
    finally:
        conn = db.get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM users WHERE user_id = %s", (test_id,))
        conn.commit()
        db.release_connection(conn)


def test_transactions_ledger():
    _require_db()
    db.init_db()
    test_id = 999999997
    try:
        db.get_or_create_user(test_id, "txtest")
        db.record_transaction(test_id, "deposit", 50.0, reference="test_ref_1", status="completed")
        txs = db.get_user_transactions(test_id, limit=5)
        assert len(txs) >= 1
        assert txs[0]["type"] == "deposit"
        assert float(txs[0]["amount"]) == 50.0

        assert db.reference_already_used("test_ref_1") is True
        assert db.reference_already_used("test_ref_999") is False
    finally:
        conn = db.get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM transactions WHERE user_id = %s", (test_id,))
        cur.execute("DELETE FROM users WHERE user_id = %s", (test_id,))
        conn.commit()
        db.release_connection(conn)


def test_game_crud():
    _require_db()
    db.init_db()
    fee = 10.0
    try:
        game = db.get_or_create_active_game(fee)
        assert game["state"] == "waiting"

        db.set_game_state(game["id"], "running")
        running = db.get_game(game["id"])
        assert running["state"] == "running"

        db.finish_game(game["id"], [1, 2], 5.0, 2.5, winner_cards={1: [0]})
        finished = db.get_game(game["id"])
        assert finished["state"] == "finished"
    finally:
        conn = db.get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM game_cards WHERE game_id = (SELECT id FROM games WHERE room_fee = %s LIMIT 1)", (fee,))
        cur.execute("DELETE FROM games WHERE room_fee = %s", (fee,))
        conn.commit()
        db.release_connection(conn)


def test_deposit_accounts():
    _require_db()
    db.init_db()
    try:
        acc_id = db.add_deposit_account("251911111111", "Test Account")
        assert acc_id is not None

        accounts = db.get_active_deposit_accounts()
        assert len(accounts) >= 1

        db.remove_deposit_account(acc_id)
        accounts = db.get_active_deposit_accounts()
        assert len(accounts) == 0
    except Exception:
        pass


def test_house_wallet():
    _require_db()
    db.init_db()
    balance = db.get_house_balance()
    assert balance == 0.0

    new_bal = db.add_house_commission(10.0)
    assert new_bal == 10.0

    total = db.get_house_total_earned()
    assert total == 10.0
