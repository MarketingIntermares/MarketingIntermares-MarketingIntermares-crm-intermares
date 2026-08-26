from __future__ import annotations

import base64
import hashlib
import os
import re
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
from cryptography.fernet import Fernet

DATABASE_URL = os.environ["DATABASE_URL"]
APP_SECRET_KEY = os.environ["APP_SECRET_KEY"]
DB_NAMESPACE = re.sub(r"[^a-zA-Z0-9_]", "_", os.getenv("DB_NAMESPACE", "crm_v2")).strip("_") or "crm_v2"

USERS_TABLE = f"{DB_NAMESPACE}_users"
SESSIONS_TABLE = f"{DB_NAMESPACE}_sessions"
SECRETS_TABLE = f"{DB_NAMESPACE}_secrets"
CAMPAIGN_RUNS_TABLE = f"{DB_NAMESPACE}_campaign_runs"
CAMPAIGN_DETAILS_TABLE = f"{DB_NAMESPACE}_campaign_details"
DAILY_CHECK_TABLE = f"{DB_NAMESPACE}_daily_check"
KV_TABLE = f"{DB_NAMESPACE}_kv_state"


def norm(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or ""))
    return "".join(c for c in text if unicodedata.category(c) != "Mn").strip().lower()


@contextmanager
def db_connection():
    with psycopg.connect(DATABASE_URL) as con:
        yield con


def db_query(sql: str, params=(), fetch: str | None = "all"):
    with db_connection() as con:
        with con.cursor() as cur:
            cur.execute(sql, params)
            if fetch == "one":
                row = cur.fetchone()
                con.commit()
                return row
            if fetch == "all":
                rows = cur.fetchall()
                con.commit()
                return rows
            con.commit()
            return None


def fernet() -> Fernet:
    if not APP_SECRET_KEY:
        raise RuntimeError("APP_SECRET_KEY não configurada")
    key = base64.urlsafe_b64encode(hashlib.sha256(APP_SECRET_KEY.encode()).digest())
    return Fernet(key)


def save_secret(key: str, value: str, updated_by: str = "system") -> None:
    encrypted = fernet().encrypt(value.strip().encode()).decode()
    db_query(
        f"""INSERT INTO {SECRETS_TABLE}(key,encrypted_value,updated_by)
        VALUES(%s,%s,%s)
        ON CONFLICT(key) DO UPDATE SET
        encrypted_value=EXCLUDED.encrypted_value,
        updated_by=EXCLUDED.updated_by,
        updated_at=NOW()""",
        (key, encrypted, updated_by),
        fetch=None,
    )


def get_secret(key: str) -> str:
    row = db_query(f"SELECT encrypted_value FROM {SECRETS_TABLE} WHERE key=%s", (key,), "one")
    if not row:
        return ""
    return fernet().decrypt(row[0].encode()).decode()


def kv_get(key: str) -> str | None:
    row = db_query(f"SELECT value FROM {KV_TABLE} WHERE key=%s", (key,), "one")
    return row[0] if row else None


def kv_set(key: str, value: Any) -> None:
    db_query(
        f"""INSERT INTO {KV_TABLE}(key,value,updated_at)
        VALUES(%s,%s,NOW())
        ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=NOW()""",
        (key, str(value)),
        fetch=None,
    )


def init_schema() -> None:
    statements = [
        f"""CREATE TABLE IF NOT EXISTS {USERS_TABLE} (
            id BIGSERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'usuario',
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
        f"""CREATE TABLE IF NOT EXISTS {SESSIONS_TABLE} (
            token_hash TEXT PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES {USERS_TABLE}(id) ON DELETE CASCADE,
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
        f"""CREATE TABLE IF NOT EXISTS {SECRETS_TABLE} (
            key TEXT PRIMARY KEY,
            encrypted_value TEXT NOT NULL,
            updated_by TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
        f"""CREATE TABLE IF NOT EXISTS {CAMPAIGN_RUNS_TABLE} (
            id BIGSERIAL PRIMARY KEY,
            run_date DATE NOT NULL DEFAULT CURRENT_DATE,
            campaign TEXT NOT NULL,
            destination TEXT,
            audience_stage TEXT,
            mode TEXT NOT NULL DEFAULT 'LIVE',
            requested INTEGER DEFAULT 0,
            selected INTEGER DEFAULT 0,
            selected_count INTEGER DEFAULT 0,
            executed_count INTEGER DEFAULT 0,
            csv_rows INTEGER DEFAULT 0,
            cards_ok INTEGER DEFAULT 0,
            errors INTEGER DEFAULT 0,
            csv_status TEXT,
            asksuite_status TEXT,
            message TEXT,
            csv_text TEXT,
            created_by TEXT NOT NULL DEFAULT 'system',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
        f"""CREATE TABLE IF NOT EXISTS {CAMPAIGN_DETAILS_TABLE} (
            id BIGSERIAL PRIMARY KEY,
            run_id BIGINT NOT NULL REFERENCES {CAMPAIGN_RUNS_TABLE}(id) ON DELETE CASCADE,
            task_id TEXT,
            lead_name TEXT,
            phone TEXT,
            seller TEXT,
            stage TEXT,
            result TEXT,
            message TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS {DAILY_CHECK_TABLE} (
            check_date DATE NOT NULL,
            campaign TEXT NOT NULL,
            mode TEXT,
            csv_rows INTEGER DEFAULT 0,
            cards_updated INTEGER DEFAULT 0,
            errors INTEGER DEFAULT 0,
            asksuite_status TEXT,
            last_update TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            observation TEXT,
            PRIMARY KEY(check_date,campaign)
        )""",
        f"""CREATE TABLE IF NOT EXISTS {KV_TABLE} (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
    ]
    for statement in statements:
        db_query(statement, fetch=None)
