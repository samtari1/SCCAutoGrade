from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Optional


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_auth_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id)")


def _hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)


def _encode_hex(raw: bytes) -> str:
    return raw.hex()


def _decode_hex(raw: str) -> bytes:
    return bytes.fromhex(raw)


def create_user(db_path: Path, email: str, password: str) -> dict:
    user_id = secrets.token_hex(16)
    created_at = int(time.time())
    salt = secrets.token_bytes(16)
    pwd_hash = _hash_password(password, salt)

    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO users(id, email, password_hash, password_salt, created_at) VALUES(?, ?, ?, ?, ?)",
            (user_id, email.lower().strip(), _encode_hex(pwd_hash), _encode_hex(salt), created_at),
        )

    return {"id": user_id, "email": email.lower().strip(), "created_at": created_at}


def authenticate_user(db_path: Path, email: str, password: str) -> Optional[dict]:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT id, email, password_hash, password_salt, created_at FROM users WHERE email = ?",
            (email.lower().strip(),),
        ).fetchone()

    if not row:
        return None

    expected_hash = _decode_hex(str(row["password_hash"]))
    salt = _decode_hex(str(row["password_salt"]))
    actual_hash = _hash_password(password, salt)
    if not hmac.compare_digest(expected_hash, actual_hash):
        return None

    return {"id": row["id"], "email": row["email"], "created_at": row["created_at"]}


def create_session(db_path: Path, user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    created_at = int(time.time())
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO sessions(token, user_id, created_at) VALUES(?, ?, ?)",
            (token, user_id, created_at),
        )
    return token


def get_user_by_token(db_path: Path, token: str) -> Optional[dict]:
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT u.id, u.email, u.created_at
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token = ?
            """,
            (token,),
        ).fetchone()

    if not row:
        return None
    return {"id": row["id"], "email": row["email"], "created_at": row["created_at"]}


def delete_session(db_path: Path, token: str) -> None:
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
