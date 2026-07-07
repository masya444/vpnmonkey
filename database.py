"""
Слой работы с базой данных (SQLite).

users: один пользователь = одна запись. sub_token — случайный токен для subscription-ссылки
(отдельный от telegram user_id специально: чтобы просто угадав чужой user_id нельзя было
получить доступ к чужой subscription-ссылке).
"""

import secrets
import sqlite3
import time
from contextlib import contextmanager
from config import config


def init_db() -> None:
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                created_at INTEGER,
                subscription_until INTEGER DEFAULT 0,
                trial_used INTEGER DEFAULT 0,
                referred_by INTEGER,
                vpn_uuid TEXT,
                server_name TEXT,
                sub_token TEXT UNIQUE
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS referral_bonuses_given (
                referrer_id INTEGER,
                referred_id INTEGER,
                given_at INTEGER,
                PRIMARY KEY (referrer_id, referred_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount INTEGER,
                days INTEGER,
                status TEXT,
                created_at INTEGER
            )
        """)
        conn.commit()


@contextmanager
def get_conn():
    conn = sqlite3.connect(config.db_path)
    try:
        yield conn
    finally:
        conn.close()


def _row_to_dict(cur, row):
    if row is None:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def get_or_create_user(user_id: int, username: str, referred_by: int | None = None) -> dict:
    with get_conn() as conn:
        cur = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        if row:
            return _row_to_dict(cur, row)

        sub_token = secrets.token_urlsafe(24)
        conn.execute(
            "INSERT INTO users (user_id, username, created_at, referred_by, sub_token) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, username, int(time.time()), referred_by, sub_token),
        )
        conn.commit()
        cur = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return _row_to_dict(cur, cur.fetchone())


def get_user(user_id: int) -> dict | None:
    with get_conn() as conn:
        cur = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return _row_to_dict(cur, cur.fetchone())


def get_user_by_sub_token(sub_token: str) -> dict | None:
    with get_conn() as conn:
        cur = conn.execute("SELECT * FROM users WHERE sub_token = ?", (sub_token,))
        return _row_to_dict(cur, cur.fetchone())


def is_subscription_active(user_id: int) -> bool:
    u = get_user(user_id)
    return bool(u and u["subscription_until"] > time.time())


def extend_subscription(user_id: int, days: int) -> int:
    now = int(time.time())
    u = get_user(user_id)
    current = u["subscription_until"] if u else 0
    base = current if current > now else now
    new_until = base + days * 86400
    with get_conn() as conn:
        conn.execute("UPDATE users SET subscription_until = ? WHERE user_id = ?", (new_until, user_id))
        conn.commit()
    return new_until


def mark_trial_used(user_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE users SET trial_used = 1 WHERE user_id = ?", (user_id,))
        conn.commit()


def save_vpn_client(user_id: int, vpn_uuid: str, server_name: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET vpn_uuid = ?, server_name = ? WHERE user_id = ?",
            (vpn_uuid, server_name, user_id),
        )
        conn.commit()


def referral_bonus_already_given(referrer_id: int, referred_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT 1 FROM referral_bonuses_given WHERE referrer_id = ? AND referred_id = ?",
            (referrer_id, referred_id),
        )
        return cur.fetchone() is not None


def mark_referral_bonus_given(referrer_id: int, referred_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO referral_bonuses_given (referrer_id, referred_id, given_at) "
            "VALUES (?, ?, ?)",
            (referrer_id, referred_id, int(time.time())),
        )
        conn.commit()


def count_referrals(user_id: int) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT COUNT(*) FROM referral_bonuses_given WHERE referrer_id = ?", (user_id,)
        )
        return cur.fetchone()[0]


def log_payment(user_id: int, amount: int, days: int, status: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO payments (user_id, amount, days, status, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, amount, days, status, int(time.time())),
        )
        conn.commit()


def get_stats() -> dict:
    """Для админ-команды /stats — быстрая сводка по проекту."""
    with get_conn() as conn:
        total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        active_subs = conn.execute(
            "SELECT COUNT(*) FROM users WHERE subscription_until > ?", (int(time.time()),)
        ).fetchone()[0]
        total_revenue = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status = 'confirmed'"
        ).fetchone()[0]
        return {
            "total_users": total_users,
            "active_subs": active_subs,
            "total_revenue": total_revenue,
        }
