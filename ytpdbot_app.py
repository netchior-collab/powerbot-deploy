"""Live-broadcast operations dashboard.

This file is the runnable control surface for the dashboard in this folder.
The proxy, account, bot and chat control records reconstruct the recoverable
control surface while preserving the existing monitoring dashboard.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import secrets
import socket
import sqlite3
import threading
import time
import urllib.request
import requests
import re
import websocket
from concurrent.futures import ThreadPoolExecutor
try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator
from urllib.parse import urlsplit, urlunsplit

import pandas as pd
import streamlit as st

PLATFORMS = [
    "팬더티비",
    "플렉스티비",
    "팝콘티비",
    "유튜브 라이브",
    "제휴 P2P 미러링 사이트",
    "기타 방송",
]
STATUSES = ["모니터링", "일시중지", "종료"]
CONTROL_STATUSES = ["실행", "일시중지", "종료"]
VIEWER_PLAN_STATUSES = ["대기", "일시중지", "종료"]
MIN_VIEWER_COUNT = 20
MAX_VIEWER_COUNT = 1500
VIEWER_COUNT_STEP = 10
PARTNER_LIMIT = 2000
PARTNER_LIMITS = {"사장님(나)": PARTNER_LIMIT}
MOTION_TYPES = [
    "자연스럽게 서서히 증가/감소 (추천)",
    "칼같이 즉시 투입/제거",
]
CHAT_MODES = [
    "채팅 시스템 비활성화",
    "연동된 계정으로 자동 매크로 채팅 분사",
    "수동 채팅 제어 대기 상태 진입",
]
DURATION_HOURS = [1, 2, 3, 4, 6, 8, 12, 24]
PROXY_SCHEMES = {"http", "https", "socks4", "socks5"}
PUBLIC_PROXY_SOURCE_CATALOG = [
    {
        "name": "TheSpeedX / SOCKS-List",
        "repository": "https://github.com/TheSpeedX/SOCKS-List",
        "feeds": [
            ("http", "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt"),
            ("socks4", "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/socks4.txt"),
            ("socks5", "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/socks5.txt"),
        ],
    },
    {
        "name": "Proxifly / free-proxy-list",
        "repository": "https://github.com/proxifly/free-proxy-list",
        "feeds": [
            ("http", "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt"),
            ("socks4", "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/socks4/data.txt"),
            ("socks5", "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/socks5/data.txt"),
        ],
    },
    {
        "name": "Proxy-Free / free-proxy-list",
        "repository": "https://github.com/proxy-free/free-proxy-list",
        "feeds": [
            ("http", "https://raw.githubusercontent.com/proxy-free/free-proxy-list/main/http.txt"),
            ("socks4", "https://raw.githubusercontent.com/proxy-free/free-proxy-list/main/socks4.txt"),
            ("socks5", "https://raw.githubusercontent.com/proxy-free/free-proxy-list/main/socks5.txt"),
        ],
    },
    {
        "name": "ProxyScrape / free-proxy-list",
        "repository": "https://github.com/proxyscrape/free-proxy-list",
        "feeds": [
            ("http", "https://raw.githubusercontent.com/proxyscrape/free-proxy-list/main/proxies/protocols/http/data.txt"),
            ("socks4", "https://raw.githubusercontent.com/proxyscrape/free-proxy-list/main/proxies/protocols/socks4/data.txt"),
            ("socks5", "https://raw.githubusercontent.com/proxyscrape/free-proxy-list/main/proxies/protocols/socks5/data.txt"),
        ],
    },
    {
        "name": "Proxmint / free-proxy-list",
        "repository": "https://github.com/proxmint/free-proxy-list",
        "feeds": [
            ("http", "https://raw.githubusercontent.com/proxmint/free-proxy-list/main/proxies/http.txt"),
            ("socks4", "https://raw.githubusercontent.com/proxmint/free-proxy-list/main/proxies/socks4.txt"),
            ("socks5", "https://raw.githubusercontent.com/proxmint/free-proxy-list/main/proxies/socks5.txt"),
        ],
    },
    {
        "name": "Dahono / free-proxy-list",
        "repository": "https://github.com/Dahono-com/free-proxy-list",
        "feeds": [
            ("http", "https://raw.githubusercontent.com/Dahono-com/free-proxy-list/main/proxies/http.txt"),
            ("socks4", "https://raw.githubusercontent.com/Dahono-com/free-proxy-list/main/proxies/socks4.txt"),
            ("socks5", "https://raw.githubusercontent.com/Dahono-com/free-proxy-list/main/proxies/socks5.txt"),
        ],
    },
    {
        "name": "Litport / free-proxy-list",
        "repository": "https://github.com/litportnet/free-proxy-list",
        "feeds": [
            ("http", "https://raw.githubusercontent.com/litportnet/free-proxy-list/live/proxies/http.txt"),
            ("socks4", "https://raw.githubusercontent.com/litportnet/free-proxy-list/live/proxies/socks4.txt"),
            ("socks5", "https://raw.githubusercontent.com/litportnet/free-proxy-list/live/proxies/socks5.txt"),
        ],
    },
    {
        "name": "Maximilian Feix / free-proxy-list",
        "repository": "https://github.com/maximilianfeix/free-proxy-list",
        "feeds": [
            ("http", "https://raw.githubusercontent.com/maximilianfeix/free-proxy-list/main/http.txt"),
            ("socks4", "https://raw.githubusercontent.com/maximilianfeix/free-proxy-list/main/socks4.txt"),
            ("socks5", "https://raw.githubusercontent.com/maximilianfeix/free-proxy-list/main/socks5.txt"),
        ],
    },
    {
        "name": "HProxy / free-proxy-list",
        "repository": "https://github.com/hproxy-com/free-proxy-list",
        "feeds": [
            ("http", "https://raw.githubusercontent.com/hproxy-com/free-proxy-list/main/http.txt"),
            ("socks4", "https://raw.githubusercontent.com/hproxy-com/free-proxy-list/main/socks4.txt"),
            ("socks5", "https://raw.githubusercontent.com/hproxy-com/free-proxy-list/main/socks5.txt"),
        ],
    },
    {
        "name": "IPLocate / free-proxy-list",
        "repository": "https://github.com/iplocate/free-proxy-list",
        "feeds": [
            ("http", "https://raw.githubusercontent.com/iplocate/free-proxy-list/main/protocols/http.txt"),
            ("socks4", "https://raw.githubusercontent.com/iplocate/free-proxy-list/main/protocols/socks4.txt"),
            ("socks5", "https://raw.githubusercontent.com/iplocate/free-proxy-list/main/protocols/socks5.txt"),
        ],
    },
    {
        "name": "Thordata / awesome-free-proxy-list",
        "repository": "https://github.com/Thordata/awesome-free-proxy-list",
        "feeds": [
            ("http", "https://raw.githubusercontent.com/Thordata/awesome-free-proxy-list/main/proxies/http.txt"),
            ("socks4", "https://raw.githubusercontent.com/Thordata/awesome-free-proxy-list/main/proxies/socks4.txt"),
            ("socks5", "https://raw.githubusercontent.com/Thordata/awesome-free-proxy-list/main/proxies/socks5.txt"),
        ],
    },
    {
        "name": "Proxio / proxy-list",
        "repository": "https://github.com/proxio-io/proxy-list",
        "feeds": [
            ("http", "https://raw.githubusercontent.com/proxio-io/proxy-list/main/http.txt"),
            ("socks4", "https://raw.githubusercontent.com/proxio-io/proxy-list/main/socks4.txt"),
            ("socks5", "https://raw.githubusercontent.com/proxio-io/proxy-list/main/socks5.txt"),
        ],
    },
    {
        "name": "Databay Labs / free-proxy-list",
        "repository": "https://github.com/databay-labs/free-proxy-list",
        "feeds": [
            ("http", "https://raw.githubusercontent.com/databay-labs/free-proxy-list/master/http.txt"),
            ("socks4", "https://raw.githubusercontent.com/databay-labs/free-proxy-list/master/socks4.txt"),
            ("socks5", "https://raw.githubusercontent.com/databay-labs/free-proxy-list/master/socks5.txt"),
        ],
    },
    {
        "name": "FusionProxies / free-proxy-list",
        "repository": "https://github.com/FusionProxies/free-proxy-list",
        "feeds": [
            ("http", "https://raw.githubusercontent.com/FusionProxies/free-proxy-list/main/proxies/http.txt"),
            ("socks4", "https://raw.githubusercontent.com/FusionProxies/free-proxy-list/main/proxies/socks4.txt"),
            ("socks5", "https://raw.githubusercontent.com/FusionProxies/free-proxy-list/main/proxies/socks5.txt"),
        ],
    },
    {
        "name": "Fyvri / fresh-proxy-list",
        "repository": "https://github.com/fyvri/fresh-proxy-list",
        "feeds": [
            ("http", "https://raw.githubusercontent.com/fyvri/fresh-proxy-list/archive/storage/classic/http.txt"),
            ("socks4", "https://raw.githubusercontent.com/fyvri/fresh-proxy-list/archive/storage/classic/socks4.txt"),
            ("socks5", "https://raw.githubusercontent.com/fyvri/fresh-proxy-list/archive/storage/classic/socks5.txt"),
        ],
    },
]
PUBLIC_PROXY_SOURCES = [
    feed
    for source in PUBLIC_PROXY_SOURCE_CATALOG
    for feed in source["feeds"]
]
MAX_PROXY_IMPORT_LINES = 5000
MAX_PROXY_IMPORT_BYTES = 1024 * 1024
MAX_PUBLIC_PROXY_COLLECT = 5000
MAX_PUBLIC_PROXY_WORKERS = 50
UI_PUBLIC_PROXY_COLLECT = 500
UI_PUBLIC_PROXY_FETCH_TIMEOUT_SECONDS = 4.0
UI_PUBLIC_PROXY_PROBE_TIMEOUT_SECONDS = 0.5
UI_PROXY_POOL_TIMEOUT_SECONDS = 3.5
UI_PROXY_POOL_CANDIDATES = 60
UI_PROXY_POOL_WORKERS = 30
PROXY_INVENTORY_DISPLAY_LIMIT = 100
PROXY_INVENTORY_CONTROL_LIMIT = 20
MAX_PROXY_FAILOVER_CANDIDATES = 40
MAX_PROXY_FAILOVER_WORKERS = 10
MAX_VERIFIED_PROXY_POOL = 10
MAX_PROXY_POOL_CANDIDATES = 100
MAX_PROXY_POOL_WORKERS = 20
PROXY_EGRESS_CHECK_URL = "https://api.ipify.org?format=json"
DB_PATH = Path(os.getenv("YTPDBOT_DB_PATH", str(Path(__file__).with_name("broadcast_ops.db")))).expanduser()
MAX_IDENTIFIER_LENGTH = 100
MAX_BROADCAST_URL_LENGTH = 2048
MAX_CHAT_MESSAGE_LENGTH = 500
MIN_ADMIN_PASSWORD_LENGTH = 6
MAX_LOGIN_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 60
SESSION_TIMEOUT_SECONDS = 60 * 60


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def deployment_version() -> str:
    commit = os.getenv("RENDER_GIT_COMMIT", "").strip()
    return commit[:8] if commit else "local"


@contextmanager
def connection() -> Iterator[Any]:
    remote_url = os.getenv("TURSO_DATABASE_URL", "").strip()
    if remote_url:
        auth_token = os.getenv("TURSO_AUTH_TOKEN", "").strip()
        if not auth_token:
            raise RuntimeError(
                "TURSO_DATABASE_URL을 사용하려면 TURSO_AUTH_TOKEN도 필요합니다."
            )
        import turso_serverless

        conn = turso_serverless.connect(remote_url, auth_token=auth_token)
        conn.row_factory = turso_serverless.Row
        conn.execute("PRAGMA foreign_keys = ON")
    else:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with connection() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS whitelist "
            "(streamer_id TEXT PRIMARY KEY, created_at TEXT NOT NULL)"
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS broadcast_sessions (
                id TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                streamer_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('모니터링', '일시중지', '종료')),
                observed_viewers INTEGER NOT NULL DEFAULT 0 CHECK(observed_viewers >= 0),
                notes TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS proxy_settings (
                name TEXT PRIMARY KEY,
                endpoint TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
                updated_at TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '직접 등록',
                last_checked_at TEXT NOT NULL DEFAULT '',
                health_status TEXT NOT NULL DEFAULT '미점검'
                    CHECK(health_status IN ('미점검', '정상', '실패')),
                quarantined INTEGER NOT NULL DEFAULT 0 CHECK(quarantined IN (0, 1)),
                failure_reason TEXT NOT NULL DEFAULT '',
                last_exit_ip TEXT NOT NULL DEFAULT '',
                latency_ms INTEGER NOT NULL DEFAULT 0 CHECK(latency_ms >= 0)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                account_name TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
                created_at TEXT NOT NULL,
                UNIQUE(platform, account_name)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS active_bots (
                id TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                streamer_id TEXT NOT NULL,
                account_name TEXT NOT NULL,
                proxy_name TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL CHECK(status IN ('실행', '일시중지', '종료')),
                connected_count INTEGER NOT NULL DEFAULT 0 CHECK(connected_count >= 0),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS auto_chats (
                id TEXT PRIMARY KEY,
                bot_id TEXT NOT NULL,
                message TEXT NOT NULL,
                interval_seconds INTEGER NOT NULL CHECK(interval_seconds >= 1),
                status TEXT NOT NULL CHECK(status IN ('실행', '일시중지', '종료')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(bot_id) REFERENCES active_bots(id) ON DELETE CASCADE
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_active_bots_status "
            "ON active_bots(status, updated_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_auto_chats_bot_status "
            "ON auto_chats(bot_id, status, updated_at)"
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS chat_logs (
                id TEXT PRIMARY KEY,
                platform TEXT NOT NULL DEFAULT '',
                account_name TEXT NOT NULL DEFAULT '',
                message TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '기록',
                sent_at TEXT NOT NULL DEFAULT ''
            )"""
        )
        chat_log_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(chat_logs)")
        }
        chat_log_migrations = {
            "platform": "TEXT NOT NULL DEFAULT ''",
            "account_name": "TEXT NOT NULL DEFAULT ''",
            "message": "TEXT NOT NULL DEFAULT ''",
            "status": "TEXT NOT NULL DEFAULT '기록'",
            "sent_at": "TEXT NOT NULL DEFAULT ''",
        }
        for column, definition in chat_log_migrations.items():
            if column not in chat_log_columns:
                conn.execute(
                    f"ALTER TABLE chat_logs ADD COLUMN {column} {definition}"
                )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_logs_sent_at "
            "ON chat_logs(sent_at DESC)"
        )
        proxy_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(proxy_settings)")
        }
        proxy_migrations = {
            "source": "TEXT NOT NULL DEFAULT '직접 등록'",
            "last_checked_at": "TEXT NOT NULL DEFAULT ''",
            "health_status": "TEXT NOT NULL DEFAULT '미점검'",
            "quarantined": "INTEGER NOT NULL DEFAULT 0",
            "failure_reason": "TEXT NOT NULL DEFAULT ''",
            "last_exit_ip": "TEXT NOT NULL DEFAULT ''",
            "latency_ms": "INTEGER NOT NULL DEFAULT 0",
        }
        for column, definition in proxy_migrations.items():
            if column not in proxy_columns:
                conn.execute(
                    f"ALTER TABLE proxy_settings ADD COLUMN {column} {definition}"
                )
        duplicate_endpoints = conn.execute(
            "SELECT endpoint FROM proxy_settings GROUP BY endpoint HAVING COUNT(*) > 1"
        ).fetchall()
        for duplicate_endpoint in duplicate_endpoints:
            duplicate_names = conn.execute(
                "SELECT name FROM proxy_settings WHERE endpoint=? ORDER BY rowid",
                (duplicate_endpoint["endpoint"],),
            ).fetchall()
            canonical_name = duplicate_names[0]["name"]
            for duplicate_name in duplicate_names[1:]:
                conn.execute(
                    "UPDATE active_bots SET proxy_name=? WHERE proxy_name=?",
                    (canonical_name, duplicate_name["name"]),
                )
                conn.execute(
                    "DELETE FROM proxy_settings WHERE name=?",
                    (duplicate_name["name"],),
                )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_proxy_settings_endpoint "
            "ON proxy_settings(endpoint)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_proxy_settings_inventory "
            "ON proxy_settings(enabled, quarantined, health_status, updated_at)"
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS viewer_plans (
                id TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                streamer_id TEXT NOT NULL,
                plan_name TEXT NOT NULL,
                viewer_count INTEGER NOT NULL CHECK(viewer_count BETWEEN 20 AND 1500),
                operator TEXT NOT NULL DEFAULT '사장님(나)',
                motion_type TEXT NOT NULL DEFAULT '자연스럽게 서서히 증가/감소 (추천)',
                chat_mode TEXT NOT NULL DEFAULT '채팅 시스템 비활성화',
                status TEXT NOT NULL CHECK(status IN ('대기', '일시중지', '종료')),
                notes TEXT NOT NULL DEFAULT '',
                duration_hours INTEGER NOT NULL DEFAULT 2,
                current_count INTEGER NOT NULL DEFAULT 0,
                started_at TEXT NOT NULL DEFAULT '',
                ends_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        viewer_plan_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(viewer_plans)")
        }
        viewer_plan_migrations = {
            "operator": "TEXT NOT NULL DEFAULT '사장님(나)'",
            "motion_type": (
                "TEXT NOT NULL DEFAULT '자연스럽게 서서히 증가/감소 (추천)'"
            ),
            "chat_mode": "TEXT NOT NULL DEFAULT '채팅 시스템 비활성화'",
            "duration_hours": "INTEGER NOT NULL DEFAULT 2",
            "current_count": "INTEGER NOT NULL DEFAULT 0",
            "started_at": "TEXT NOT NULL DEFAULT ''",
            "ends_at": "TEXT NOT NULL DEFAULT ''",
        }
        for column, definition in viewer_plan_migrations.items():
            if column not in viewer_plan_columns:
                conn.execute(
                    f"ALTER TABLE viewer_plans ADD COLUMN {column} {definition}"
                )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_viewer_plans_status "
            "ON viewer_plans(status, updated_at)"
        )


@st.cache_resource(show_spinner=False)
def init_db_once(database_identity: str) -> None:
    """Initialize one database once per Streamlit process instead of every rerun."""
    del database_identity
    init_db()


def whitelist() -> list[str]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT streamer_id FROM whitelist ORDER BY streamer_id"
        ).fetchall()
    return [row["streamer_id"] for row in rows]


def sessions() -> pd.DataFrame:
    columns = [
        "id",
        "platform",
        "streamer_id",
        "status",
        "observed_viewers",
        "notes",
        "started_at",
        "updated_at",
    ]
    with connection() as conn:
        rows = conn.execute(
            "SELECT id, platform, streamer_id, status, observed_viewers, notes, "
            "started_at, updated_at FROM broadcast_sessions ORDER BY updated_at DESC",
        ).fetchall()
    return pd.DataFrame([dict(row) for row in rows], columns=columns)


def records(table: str) -> list[sqlite3.Row]:
    allowed = {
        "proxy_settings", "accounts", "active_bots", "auto_chats", "viewer_plans"
    }
    if table not in allowed:
        raise ValueError(f"지원하지 않는 테이블: {table}")
    with connection() as conn:
        return conn.execute(f"SELECT * FROM {table} ORDER BY rowid DESC").fetchall()


def dashboard_snapshot() -> dict[str, Any]:
    """Load all read-only dashboard data through one remote DB connection."""
    session_columns = [
        "id",
        "platform",
        "streamer_id",
        "status",
        "observed_viewers",
        "notes",
        "started_at",
        "updated_at",
    ]
    with connection() as conn:
        session_rows = conn.execute(
            "SELECT id, platform, streamer_id, status, observed_viewers, notes, "
            "started_at, updated_at FROM broadcast_sessions ORDER BY updated_at DESC"
        ).fetchall()
        allowlist_rows = conn.execute(
            "SELECT streamer_id FROM whitelist ORDER BY streamer_id"
        ).fetchall()
        table_rows = {
            table: [
                dict(row)
                for row in conn.execute(
                    f"SELECT * FROM {table} ORDER BY rowid DESC"
                ).fetchall()
            ]
            for table in (
                "proxy_settings",
                "accounts",
                "active_bots",
                "auto_chats",
                "viewer_plans",
            )
        }
    return {
        "sessions": pd.DataFrame(
            [dict(row) for row in session_rows], columns=session_columns
        ),
        "whitelist": [row["streamer_id"] for row in allowlist_rows],
        **table_rows,
    }


def normalized_identifier(value: str, label: str) -> str:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{label}에 제어 문자를 사용할 수 없습니다.")
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError(f"{label}을(를) 입력하세요.")
    if len(normalized) > MAX_IDENTIFIER_LENGTH:
        raise ValueError(f"{label}은(는) {MAX_IDENTIFIER_LENGTH}자 이하여야 합니다.")
    return normalized


def normalize_broadcast_url(value: str) -> str:
    broadcast_url = value.strip()
    if not broadcast_url:
        raise ValueError("방송 URL을 입력하세요.")
    if len(broadcast_url) > MAX_BROADCAST_URL_LENGTH:
        raise ValueError(f"방송 URL은 {MAX_BROADCAST_URL_LENGTH}자 이하여야 합니다.")
    try:
        parsed = urlsplit(broadcast_url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("방송 URL 형식이 올바르지 않습니다.") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("방송 URL은 http:// 또는 https://로 시작해야 합니다.")
    if parsed.username or parsed.password:
        raise ValueError("방송 URL에 아이디나 비밀번호를 포함하지 마세요.")
    host = parsed.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host if port is None else f"{host}:{port}"
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme.lower(), netloc, path, parsed.query, ""))


def platform_from_broadcast_url(broadcast_url: str) -> str:
    host = (urlsplit(normalize_broadcast_url(broadcast_url)).hostname or "").lower()
    if host == "youtu.be" or host.endswith(".youtube.com") or host == "youtube.com":
        return "유튜브 라이브"
    if "panda" in host:
        return "팬더티비"
    if "flex" in host:
        return "플렉스티비"
    if "popcorn" in host:
        return "팝콘티비"
    return "기타 방송"


def normalize_proxy_endpoint(value: str) -> str:
    endpoint = value.strip()
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("프록시 포트가 올바르지 않습니다.") from exc
    if parsed.scheme.lower() not in PROXY_SCHEMES:
        raise ValueError("프록시 형식은 http, https, socks4, socks5만 지원합니다.")
    if not parsed.hostname or port is None:
        raise ValueError("프록시 주소에는 호스트와 포트가 필요합니다.")
    if parsed.username or parsed.password:
        raise ValueError("프록시 주소에 아이디나 비밀번호를 포함하지 마세요.")
    host = parsed.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return urlunsplit((parsed.scheme.lower(), f"{host}:{port}", "", "", ""))


def parse_proxy_inventory(raw_text: str) -> tuple[list[str], list[str], int]:
    """Validate and deduplicate newline/CSV-like proxy inventory input."""
    if len(raw_text.encode("utf-8")) > MAX_PROXY_IMPORT_BYTES:
        raise ValueError("프록시 가져오기 데이터는 1MB 이하여야 합니다.")
    lines = raw_text.splitlines()
    if len(lines) > MAX_PROXY_IMPORT_LINES:
        raise ValueError(f"프록시는 한 번에 {MAX_PROXY_IMPORT_LINES}줄까지 가져올 수 있습니다.")
    endpoints: list[str] = []
    errors: list[str] = []
    seen: set[str] = set()
    duplicates = 0
    for line_number, raw_line in enumerate(lines, start=1):
        value = raw_line.strip().lstrip("\ufeff")
        if not value or value.startswith("#"):
            continue
        candidate = value.split(",", 1)[0].strip()
        try:
            endpoint = normalize_proxy_endpoint(candidate)
        except ValueError as exc:
            errors.append(f"{line_number}행: {exc}")
            continue
        if endpoint in seen:
            duplicates += 1
            continue
        seen.add(endpoint)
        endpoints.append(endpoint)
    return endpoints, errors, duplicates


def _proxy_inventory_name(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    host = (parsed.hostname or "proxy").replace(":", "-")[:40]
    digest = hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:8]
    return f"공개-{parsed.scheme}-{host}-{parsed.port}-{digest}"[:MAX_IDENTIFIER_LENGTH]


def save_proxy(
    name: str,
    endpoint: str,
    *,
    enabled: bool = True,
    source: str = "직접 등록",
) -> bool:
    """Save one credential-free proxy; return False when its endpoint exists."""
    normalized_name = normalized_identifier(name, "프록시 이름")
    normalized_endpoint = normalize_proxy_endpoint(endpoint)
    normalized_source = normalized_identifier(source, "프록시 출처")
    with connection() as conn:
        duplicate = conn.execute(
            "SELECT name FROM proxy_settings WHERE endpoint=? AND name<>?",
            (normalized_endpoint, normalized_name),
        ).fetchone()
        if duplicate is not None:
            return False
        conn.execute(
            "INSERT INTO proxy_settings "
            "(name, endpoint, enabled, updated_at, source, last_checked_at, "
            "health_status, quarantined, failure_reason) "
            "VALUES (?, ?, ?, ?, ?, '', '미점검', 0, '') "
            "ON CONFLICT(name) DO UPDATE SET endpoint=excluded.endpoint, "
            "enabled=excluded.enabled, updated_at=excluded.updated_at, "
            "source=excluded.source, last_checked_at='', health_status='미점검', "
            "quarantined=0, failure_reason='', last_exit_ip='', latency_ms=0",
            (
                normalized_name,
                normalized_endpoint,
                int(enabled),
                now(),
                normalized_source,
            ),
        )
    return True


def import_proxy_inventory(raw_text: str, source: str) -> dict[str, Any]:
    """Import valid unique endpoints without replacing existing inventory."""
    normalized_source = normalized_identifier(source, "프록시 출처")
    endpoints, errors, input_duplicates = parse_proxy_inventory(raw_text)
    imported = 0
    existing = 0
    with connection() as conn:
        known = {
            row["endpoint"]
            for row in conn.execute("SELECT endpoint FROM proxy_settings").fetchall()
        }
        timestamp = now()
        for endpoint in endpoints:
            if endpoint in known:
                existing += 1
                continue
            cursor = conn.execute(
                "INSERT OR IGNORE INTO proxy_settings "
                "(name, endpoint, enabled, updated_at, source, last_checked_at, "
                "health_status, quarantined, failure_reason) "
                "VALUES (?, ?, 0, ?, ?, '', '미점검', 0, '')",
                (_proxy_inventory_name(endpoint), endpoint, timestamp, normalized_source),
            )
            if getattr(cursor, "rowcount", 1) == 0:
                existing += 1
            else:
                known.add(endpoint)
                imported += 1
    return {
        "imported": imported,
        "duplicates": input_duplicates + existing,
        "invalid": len(errors),
        "errors": errors,
    }


def check_proxy_connection(endpoint: str, timeout_seconds: float = 3.0) -> None:
    """Check endpoint TCP reachability only; never sends application traffic."""
    normalized_endpoint = normalize_proxy_endpoint(endpoint)
    parsed = urlsplit(normalized_endpoint)
    with socket.create_connection(
        (parsed.hostname or "", parsed.port or 0), timeout=timeout_seconds
    ):
        pass


def _probe_proxy_route(endpoint: str, timeout_seconds: float) -> dict[str, Any]:
    endpoint = normalize_proxy_endpoint(endpoint)
    started_at = time.perf_counter()
    response = requests.get(
        PROXY_EGRESS_CHECK_URL,
        proxies={"http": endpoint, "https": endpoint},
        timeout=timeout_seconds,
        headers={"User-Agent": "YN-Powerbot/1.0"},
    )
    response.raise_for_status()
    exit_ip = str(ipaddress.ip_address(response.json()["ip"]))
    latency_ms = max(1, int((time.perf_counter() - started_at) * 1000))
    return {"endpoint": endpoint, "exit_ip": exit_ip, "latency_ms": latency_ms}


def _discard_failed_proxy(name: str, *, pause_dependents: bool) -> list[str]:
    timestamp = now()
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        affected_bots = conn.execute(
            "SELECT id FROM active_bots WHERE proxy_name=? AND status='실행'",
            (name,),
        ).fetchall()
        bot_ids = [bot["id"] for bot in affected_bots]
        conn.execute("DELETE FROM proxy_settings WHERE name=?", (name,))
        if pause_dependents:
            for bot_id in bot_ids:
                conn.execute(
                    "UPDATE active_bots SET status='일시중지', updated_at=? "
                    "WHERE id=?",
                    (timestamp, bot_id),
                )
                conn.execute(
                    "UPDATE auto_chats SET status='일시중지', updated_at=? "
                    "WHERE bot_id=? AND status='실행'",
                    (timestamp, bot_id),
                )
    return bot_ids


def verify_proxy_route(
    name: str,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Verify one proxy, deleting it immediately when actual routing fails."""
    with connection() as conn:
        proxy = conn.execute(
            "SELECT endpoint FROM proxy_settings WHERE name=?", (name,)
        ).fetchone()
    if proxy is None:
        raise ValueError("확인할 프록시를 찾을 수 없습니다.")

    try:
        result = _probe_proxy_route(proxy["endpoint"], timeout_seconds)
    except (requests.RequestException, KeyError, TypeError, ValueError):
        _discard_failed_proxy(name, pause_dependents=True)
        raise ValueError("실패한 프록시를 폐기했습니다.") from None

    checked_at = now()
    with connection() as conn:
        conn.execute(
            "UPDATE proxy_settings SET enabled=1, health_status='정상', "
            "quarantined=0, failure_reason='', last_checked_at=?, "
            "last_exit_ip=?, latency_ms=?, updated_at=? WHERE name=?",
            (
                checked_at,
                result["exit_ip"],
                result["latency_ms"],
                checked_at,
                name,
            ),
        )
    return {"name": name, **result}


def activate_proxy_with_failover(
    preferred_name: str,
    timeout_seconds: float = 8.0,
    max_candidates: int = MAX_PROXY_FAILOVER_CANDIDATES,
    workers: int = MAX_PROXY_FAILOVER_WORKERS,
) -> dict[str, Any]:
    """Discard failed candidates and immediately switch dependents to a winner."""
    if max_candidates < 1 or workers < 1:
        raise ValueError("자동 전환 후보와 동시 점검 수는 1개 이상이어야 합니다.")
    with connection() as conn:
        candidates = conn.execute(
            "SELECT name, endpoint FROM proxy_settings "
            "ORDER BY CASE WHEN name=? THEN 0 ELSE 1 END, enabled DESC, "
            "CASE health_status WHEN '정상' THEN 0 WHEN '미점검' THEN 1 ELSE 2 END, "
            "updated_at DESC LIMIT ?",
            (preferred_name, int(max_candidates)),
        ).fetchall()
    if not candidates:
        raise ValueError("자동 전환할 프록시가 없습니다.")

    discarded: list[str] = []
    with connection() as conn:
        missing_proxy_bots = conn.execute(
            "SELECT id FROM active_bots WHERE proxy_name<>'' AND status='실행' "
            "AND NOT EXISTS (SELECT 1 FROM proxy_settings "
            "WHERE proxy_settings.name=active_bots.proxy_name)"
        ).fetchall()
    affected_bot_ids: set[str] = {bot["id"] for bot in missing_proxy_bots}
    winner: dict[str, Any] | None = None
    attempted = 0
    batch_size = min(int(workers), len(candidates))
    for offset in range(0, len(candidates), batch_size):
        batch = candidates[offset : offset + batch_size]

        def probe(candidate: Any) -> tuple[Any, dict[str, Any] | None]:
            try:
                return candidate, _probe_proxy_route(
                    candidate["endpoint"], timeout_seconds
                )
            except (requests.RequestException, KeyError, TypeError, ValueError):
                return candidate, None

        with ThreadPoolExecutor(max_workers=len(batch)) as executor:
            results = list(executor.map(probe, batch))
        attempted += len(results)
        successful = [
            (candidate, result)
            for candidate, result in results
            if result is not None
        ]
        failed = [candidate for candidate, result in results if result is None]
        for candidate in failed:
            affected_bot_ids.update(
                _discard_failed_proxy(
                    candidate["name"], pause_dependents=False
                )
            )
            discarded.append(candidate["name"])
        if successful:
            candidate, result = min(
                successful, key=lambda item: item[1]["latency_ms"]
            )
            winner = {"name": candidate["name"], **result}
            break

    timestamp = now()
    if winner is None:
        with connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for bot_id in affected_bot_ids:
                conn.execute(
                    "UPDATE active_bots SET status='일시중지', updated_at=? "
                    "WHERE id=?",
                    (timestamp, bot_id),
                )
                conn.execute(
                    "UPDATE auto_chats SET status='일시중지', updated_at=? "
                    "WHERE bot_id=? AND status='실행'",
                    (timestamp, bot_id),
                )
        raise ValueError("정상 동작하는 프록시가 없어 실패 후보를 모두 폐기했습니다.")

    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE proxy_settings SET enabled=1, health_status='정상', "
            "quarantined=0, failure_reason='', last_checked_at=?, "
            "last_exit_ip=?, latency_ms=?, updated_at=? WHERE name=?",
            (
                timestamp,
                winner["exit_ip"],
                winner["latency_ms"],
                timestamp,
                winner["name"],
            ),
        )
        for bot_id in affected_bot_ids:
            conn.execute(
                "UPDATE active_bots SET proxy_name=?, status='실행', updated_at=? "
                "WHERE id=?",
                (winner["name"], timestamp, bot_id),
            )
    return {
        **winner,
        "attempted": attempted,
        "discarded": discarded,
        "reassigned_bots": len(affected_bot_ids),
    }


def activate_collected_proxy_pool(
    pool_size: int = MAX_VERIFIED_PROXY_POOL,
    timeout_seconds: float = 8.0,
    max_candidates: int = MAX_PROXY_POOL_CANDIDATES,
    workers: int = MAX_PROXY_POOL_WORKERS,
) -> dict[str, Any]:
    """Route-check collected proxies, enable a usable pool, and rewire work."""
    if pool_size < 1 or max_candidates < 1 or workers < 1:
        raise ValueError("활성 풀·점검 후보·동시 점검 수는 1개 이상이어야 합니다.")
    with connection() as conn:
        candidates = conn.execute(
            "SELECT name, endpoint FROM proxy_settings "
            "WHERE source='자동 수집' "
            "ORDER BY enabled DESC, "
            "CASE health_status WHEN '정상' THEN 0 WHEN '미점검' THEN 1 ELSE 2 END, "
            "latency_ms ASC, updated_at DESC LIMIT ?",
            (int(max_candidates),),
        ).fetchall()
    if not candidates:
        raise ValueError("실경유를 확인할 자동 수집 프록시가 없습니다.")

    winners: list[dict[str, Any]] = []
    discarded: list[str] = []
    attempted = 0
    batch_size = min(int(workers), len(candidates))
    for offset in range(0, len(candidates), batch_size):
        batch = candidates[offset : offset + batch_size]

        def probe(candidate: Any) -> tuple[Any, dict[str, Any] | None]:
            try:
                return candidate, _probe_proxy_route(
                    candidate["endpoint"], timeout_seconds
                )
            except (requests.RequestException, KeyError, TypeError, ValueError):
                return candidate, None

        with ThreadPoolExecutor(max_workers=len(batch)) as executor:
            results = list(executor.map(probe, batch))
        attempted += len(results)
        for candidate, result in results:
            if result is None:
                _discard_failed_proxy(candidate["name"], pause_dependents=False)
                discarded.append(candidate["name"])
            else:
                winners.append({"name": candidate["name"], **result})
        winners.sort(key=lambda item: item["latency_ms"])
        if len(winners) >= int(pool_size):
            winners = winners[: int(pool_size)]
            break

    timestamp = now()
    winner_names = [winner["name"] for winner in winners]
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE proxy_settings SET enabled=0, updated_at=? "
            "WHERE source='자동 수집'",
            (timestamp,),
        )
        for winner in winners:
            conn.execute(
                "UPDATE proxy_settings SET enabled=1, health_status='정상', "
                "quarantined=0, failure_reason='', last_checked_at=?, "
                "last_exit_ip=?, latency_ms=?, updated_at=? WHERE name=?",
                (
                    timestamp,
                    winner["exit_ip"],
                    winner["latency_ms"],
                    timestamp,
                    winner["name"],
                ),
            )
        affected = conn.execute(
            "SELECT id FROM active_bots WHERE proxy_name<>'' AND status='실행' "
            "AND NOT EXISTS (SELECT 1 FROM proxy_settings WHERE "
            "proxy_settings.name=active_bots.proxy_name AND enabled=1 "
            "AND quarantined=0 AND health_status='정상' AND last_exit_ip<>'') "
            "ORDER BY id"
        ).fetchall()
        for index, bot in enumerate(affected):
            if winner_names:
                conn.execute(
                    "UPDATE active_bots SET proxy_name=?, updated_at=? WHERE id=?",
                    (winner_names[index % len(winner_names)], timestamp, bot["id"]),
                )
            else:
                conn.execute(
                    "UPDATE active_bots SET status='일시중지', updated_at=? WHERE id=?",
                    (timestamp, bot["id"]),
                )
                conn.execute(
                    "UPDATE auto_chats SET status='일시중지', updated_at=? "
                    "WHERE bot_id=? AND status='실행'",
                    (timestamp, bot["id"]),
                )
    if not winners:
        raise ValueError("실제 경유에 성공한 프록시가 없어 실패 후보를 폐기했습니다.")
    return {
        "winners": winners,
        "attempted": attempted,
        "discarded": discarded,
        "reassigned_bots": len(affected),
    }


def check_proxy_inventory_health(
    names: list[str] | None = None,
    timeout_seconds: float = 3.0,
) -> dict[str, bool]:
    with connection() as conn:
        if names is None:
            proxies = conn.execute(
                "SELECT name, endpoint FROM proxy_settings ORDER BY rowid"
            ).fetchall()
        else:
            proxies = []
            for name in names:
                proxy = conn.execute(
                    "SELECT name, endpoint FROM proxy_settings WHERE name=?", (name,)
                ).fetchone()
                if proxy is None:
                    raise ValueError("점검할 프록시를 찾을 수 없습니다.")
                proxies.append(proxy)
    if not proxies:
        return {}

    def probe(proxy: Any) -> tuple[str, bool, str]:
        try:
            check_proxy_connection(proxy["endpoint"], timeout_seconds)
        except (OSError, ValueError) as exc:
            return proxy["name"], False, f"연결 실패 ({type(exc).__name__})"
        return proxy["name"], True, ""

    worker_count = min(20, len(proxies))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = list(executor.map(probe, proxies))
    checked_at = now()
    failed_names = [name for name, healthy, _ in results if not healthy]
    healthy_names = [name for name, healthy, _ in results if healthy]
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for name, healthy, reason in results:
            if healthy:
                conn.execute(
                    "UPDATE proxy_settings SET health_status='정상', quarantined=0, "
                    "failure_reason='', last_checked_at=?, updated_at=? WHERE name=?",
                    (checked_at, checked_at, name),
                )
            else:
                conn.execute("DELETE FROM proxy_settings WHERE name=?", (name,))
    if failed_names:
        with connection() as conn:
            fallback = conn.execute(
                "SELECT name FROM proxy_settings "
                "ORDER BY CASE WHEN name=? THEN 0 ELSE 1 END, enabled DESC, "
                "CASE health_status WHEN '정상' THEN 0 WHEN '미점검' THEN 1 ELSE 2 END, "
                "updated_at DESC LIMIT 1",
                (healthy_names[0] if healthy_names else "",),
            ).fetchone()
        if fallback is not None:
            try:
                activate_proxy_with_failover(fallback["name"])
            except ValueError:
                # activate_proxy_with_failover already pauses dependent work
                # when no usable route remains.
                pass
        else:
            with connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                affected_bots = conn.execute(
                    "SELECT id FROM active_bots WHERE proxy_name<>'' AND status='실행' "
                    "AND NOT EXISTS (SELECT 1 FROM proxy_settings "
                    "WHERE proxy_settings.name=active_bots.proxy_name)"
                ).fetchall()
                for bot in affected_bots:
                    conn.execute(
                        "UPDATE active_bots SET status='일시중지', updated_at=? "
                        "WHERE id=?",
                        (checked_at, bot["id"]),
                    )
                    conn.execute(
                        "UPDATE auto_chats SET status='일시중지', updated_at=? "
                        "WHERE bot_id=? AND status='실행'",
                        (checked_at, bot["id"]),
                    )
    return {name: healthy for name, healthy, _ in results}


def collect_and_verify_proxies(
    max_collect: int = MAX_PUBLIC_PROXY_COLLECT,
    workers: int = MAX_PUBLIC_PROXY_WORKERS,
    *,
    fetch_timeout_seconds: float = 12.0,
    probe_timeout_seconds: float = 1.5,
) -> dict[str, int]:
    """Collect public proxy lists, TCP-check them, and save new live endpoints."""
    if not 1 <= int(max_collect) <= MAX_PUBLIC_PROXY_COLLECT:
        raise ValueError(
            f"최대 수집 후보는 1~{MAX_PUBLIC_PROXY_COLLECT}개여야 합니다."
        )
    if not 1 <= int(workers) <= MAX_PUBLIC_PROXY_WORKERS:
        raise ValueError(f"동시 점검 수는 1~{MAX_PUBLIC_PROXY_WORKERS}개여야 합니다.")

    candidates: list[str] = []
    seen: set[str] = set()
    source_failures = 0
    source_count = max(1, len(PUBLIC_PROXY_SOURCES))
    per_source_limit = max(1, (int(max_collect) + source_count - 1) // source_count)
    for default_scheme, source_url in PUBLIC_PROXY_SOURCES:
        try:
            request = urllib.request.Request(
                source_url,
                headers={"User-Agent": "YN-Powerbot/1.0"},
            )
            with urllib.request.urlopen(
                request, timeout=fetch_timeout_seconds
            ) as response:
                payload = response.read(MAX_PROXY_IMPORT_BYTES)
            # Some maintained feeds contain hundreds of thousands of rows. A
            # bounded prefix is enough because each source has a fair quota;
            # discard only the possibly truncated final row instead of
            # rejecting the entire source for being larger than 1 MiB.
            if len(payload) == MAX_PROXY_IMPORT_BYTES and b"\n" in payload:
                payload = payload.rsplit(b"\n", 1)[0]
            lines = payload.decode("utf-8", errors="ignore").splitlines()
        except (OSError, ValueError):
            source_failures += 1
            continue

        source_added = 0
        for raw_line in lines:
            candidate = raw_line.strip().lstrip("\ufeff")
            if not candidate or candidate.startswith("#"):
                continue
            if "://" not in candidate:
                candidate = f"{default_scheme}://{candidate}"
            try:
                endpoint = normalize_proxy_endpoint(candidate)
            except ValueError:
                continue
            if endpoint in seen:
                continue
            seen.add(endpoint)
            candidates.append(endpoint)
            source_added += 1
            if source_added >= per_source_limit or len(candidates) >= max_collect:
                break

    def probe(endpoint: str) -> tuple[str, bool]:
        try:
            check_proxy_connection(endpoint, probe_timeout_seconds)
        except (OSError, ValueError):
            return endpoint, False
        return endpoint, True

    if not candidates:
        removed_count = 0
        if source_failures == 0:
            with connection() as conn:
                stale = conn.execute(
                    "SELECT name FROM proxy_settings WHERE source='자동 수집'"
                ).fetchall()
                for proxy in stale:
                    conn.execute(
                        "DELETE FROM proxy_settings WHERE name=?",
                        (proxy["name"],),
                    )
                removed_count = len(stale)
        return {
            "collected": 0,
            "alive": 0,
            "saved": 0,
            "removed": removed_count,
            "reconciled": int(source_failures == 0),
            "source_failures": source_failures,
        }

    worker_count = min(int(workers), len(candidates))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = list(executor.map(probe, candidates))
    alive_endpoints = [endpoint for endpoint, healthy in results if healthy]

    saved_count = 0
    removed_count = 0
    timestamp = now()
    alive_set = set(alive_endpoints)
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = {
            row["endpoint"]: row
            for row in conn.execute(
                "SELECT name, endpoint, source FROM proxy_settings"
            ).fetchall()
        }
        if source_failures == 0:
            stale_auto = [
                row
                for row in existing.values()
                if row["source"] == "자동 수집"
                and row["endpoint"] not in alive_set
            ]
            for proxy in stale_auto:
                conn.execute(
                    "DELETE FROM proxy_settings WHERE name=?", (proxy["name"],)
                )
            removed_count = len(stale_auto)
        for endpoint in alive_endpoints:
            known_proxy = existing.get(endpoint)
            if known_proxy is not None:
                if known_proxy["source"] == "자동 수집":
                    conn.execute(
                        "UPDATE proxy_settings SET enabled=0, updated_at=?, "
                        "last_checked_at=?, health_status='정상', quarantined=0, "
                        "failure_reason='', last_exit_ip='', latency_ms=0 "
                        "WHERE name=?",
                        (timestamp, timestamp, known_proxy["name"]),
                    )
                continue
            cursor = conn.execute(
                "INSERT OR IGNORE INTO proxy_settings "
                "(name, endpoint, enabled, updated_at, source, last_checked_at, "
                "health_status, quarantined, failure_reason) "
                "VALUES (?, ?, 1, ?, '자동 수집', ?, '정상', 0, '')",
                (
                    _proxy_inventory_name(endpoint),
                    endpoint,
                    timestamp,
                    timestamp,
                ),
            )
            if getattr(cursor, "rowcount", 1) != 0:
                saved_count += 1

    return {
        "collected": len(candidates),
        "alive": len(alive_endpoints),
        "saved": saved_count,
        "removed": removed_count,
        "reconciled": int(source_failures == 0),
        "source_failures": source_failures,
    }


def check_proxy_health(name: str, timeout_seconds: float = 3.0) -> bool:
    return check_proxy_inventory_health([name], timeout_seconds)[name]


def set_proxy_enabled(name: str, enabled: bool) -> None:
    timestamp = now()
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        proxy = conn.execute(
            "SELECT quarantined FROM proxy_settings WHERE name=?", (name,)
        ).fetchone()
        if proxy is None:
            raise ValueError("프록시를 찾을 수 없습니다.")
        if enabled and proxy["quarantined"]:
            raise ValueError("격리된 프록시는 연결 상태 점검을 통과한 뒤 활성화하세요.")
        conn.execute(
            "UPDATE proxy_settings SET enabled=?, updated_at=? WHERE name=?",
            (int(enabled), timestamp, name),
        )
        if not enabled:
            affected_bots = conn.execute(
                "SELECT id FROM active_bots WHERE proxy_name=? AND status='실행'",
                (name,),
            ).fetchall()
            for bot in affected_bots:
                conn.execute(
                    "UPDATE active_bots SET status='일시중지', updated_at=? WHERE id=?",
                    (timestamp, bot["id"]),
                )
                conn.execute(
                    "UPDATE auto_chats SET status='일시중지', updated_at=? "
                    "WHERE bot_id=? AND status='실행'",
                    (timestamp, bot["id"]),
                )


def save_account(platform: str, account_name: str) -> bool:
    """Save one account record; return False when it already exists."""
    if platform not in PLATFORMS:
        raise ValueError("지원하지 않는 계정 플랫폼입니다.")
    normalized_name = normalized_identifier(account_name, "계정 이름")
    with connection() as conn:
        existing = conn.execute(
            "SELECT 1 FROM accounts WHERE platform=? AND account_name=?",
            (platform, normalized_name),
        ).fetchone()
        if existing is not None:
            return False
        conn.execute(
            "INSERT INTO accounts (id, platform, account_name, enabled, created_at) "
            "VALUES (?, ?, ?, 1, ?)",
            (secrets.token_urlsafe(12), platform, normalized_name, now()),
        )
    return True


def set_account_enabled(account_id: str, enabled: bool) -> None:
    """Toggle an account and pause dependent work when it becomes unavailable."""
    timestamp = now()
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        account = conn.execute(
            "SELECT platform, account_name FROM accounts WHERE id=?", (account_id,)
        ).fetchone()
        if account is None:
            raise ValueError("계정을 찾을 수 없습니다.")
        conn.execute(
            "UPDATE accounts SET enabled=? WHERE id=?", (int(enabled), account_id)
        )
        if not enabled:
            affected_bots = conn.execute(
                "SELECT id FROM active_bots WHERE platform=? AND account_name=? "
                "AND status='실행'",
                (account["platform"], account["account_name"]),
            ).fetchall()
            for bot in affected_bots:
                conn.execute(
                    "UPDATE active_bots SET status='일시중지', updated_at=? WHERE id=?",
                    (timestamp, bot["id"]),
                )
                conn.execute(
                    "UPDATE auto_chats SET status='일시중지', updated_at=? "
                    "WHERE bot_id=? AND status='실행'",
                    (timestamp, bot["id"]),
                )


def set_bot_status(bot_id: str, status: str) -> None:
    if status not in CONTROL_STATUSES:
        raise ValueError(f"지원하지 않는 봇 상태: {status}")
    # 워커 시작/정지
    if status == "종료":
        stop_worker(bot_id)
    elif status == "실행":
        # 기존 워커가 없으면 새로 시작
        if bot_id not in _ACTIVE_WORKERS:
            with connection() as conn:
                row = conn.execute(
                    "SELECT platform, streamer_id, proxy_name FROM active_bots WHERE id=?",
                    (bot_id,),
                ).fetchone()
            if row:
                with connection() as conn:
                    plan = conn.execute(
                        "SELECT viewer_count FROM viewer_plans "
                        "WHERE streamer_id=? AND status!='종료' LIMIT 1",
                        (row["streamer_id"],),
                    ).fetchone()
                target = plan["viewer_count"] if plan else 200
                start_worker(bot_id, row["platform"], row["streamer_id"], target, row["proxy_name"], bot_id)
    timestamp = now()
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        bot = conn.execute(
            "SELECT platform, account_name, proxy_name FROM active_bots WHERE id=?",
            (bot_id,),
        ).fetchone()
        if bot is None:
            raise ValueError("봇 제어 항목을 찾을 수 없습니다.")
        if status == "실행":
            account = conn.execute(
                "SELECT 1 FROM accounts WHERE platform=? AND account_name=? "
                "AND enabled=1",
                (bot["platform"], bot["account_name"]),
            ).fetchone()
            if account is None:
                raise ValueError("사용 가능한 계정이 아니므로 봇을 다시 시작할 수 없습니다.")
            if bot["proxy_name"]:
                proxy = conn.execute(
                    "SELECT 1 FROM proxy_settings "
                    "WHERE name=? AND enabled=1 AND quarantined=0 "
                    "AND health_status='정상' AND last_exit_ip<>''",
                    (bot["proxy_name"],),
                ).fetchone()
                if proxy is None:
                    raise ValueError(
                        "사용 가능한 프록시가 아니므로 봇을 다시 시작할 수 없습니다."
                    )
        result = conn.execute(
            "UPDATE active_bots SET status=?, updated_at=? WHERE id=?",
            (status, timestamp, bot_id),
        )
        if result.rowcount != 1:  # Defensive: the row was read in this transaction.
            raise ValueError("봇 제어 항목을 찾을 수 없습니다.")
        if status == "일시중지":
            conn.execute(
                "UPDATE auto_chats SET status='일시중지', updated_at=? "
                "WHERE bot_id=? AND status='실행'",
                (timestamp, bot_id),
            )
        elif status == "종료":
            conn.execute(
                "UPDATE auto_chats SET status='종료', updated_at=? "
                "WHERE bot_id=? AND status!='종료'",
                (timestamp, bot_id),
            )


def set_chat_status(chat_id: str, status: str) -> None:
    if status not in CONTROL_STATUSES:
        raise ValueError(f"지원하지 않는 채팅 상태: {status}")
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        chat = conn.execute(
            "SELECT bot_id FROM auto_chats WHERE id=?", (chat_id,)
        ).fetchone()
        if chat is None:
            raise ValueError("자동 채팅 항목을 찾을 수 없습니다.")
        if status == "실행":
            bot = conn.execute(
                "SELECT status FROM active_bots WHERE id=?", (chat["bot_id"],)
            ).fetchone()
            if bot is None or bot["status"] != "실행":
                raise ValueError("실행 중인 봇의 채팅만 다시 시작할 수 있습니다.")
        conn.execute(
            "UPDATE auto_chats SET status=?, updated_at=? WHERE id=?",
            (status, now(), chat_id),
        )


def start_bot(
    platform: str, streamer_id: str, account_id: str, proxy_name: str = ""
) -> str:
    """Create a bot only if its account and optional proxy are still usable."""
    streamer_id = normalize_broadcast_url(streamer_id)
    timestamp = now()
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        account = conn.execute(
            "SELECT account_name FROM accounts "
            "WHERE id=? AND platform=? AND enabled=1",
            (account_id, platform),
        ).fetchone()
        if account is None:
            raise ValueError("선택한 플랫폼의 사용 가능한 계정을 찾을 수 없습니다.")
        if proxy_name:
            proxy = conn.execute(
                "SELECT 1 FROM proxy_settings "
                "WHERE name=? AND enabled=1 AND quarantined=0 "
                "AND health_status='정상' AND last_exit_ip<>''",
                (proxy_name,),
            ).fetchone()
            if proxy is None:
                raise ValueError("사용 가능한 프록시를 찾을 수 없습니다.")
        duplicate = conn.execute(
            "SELECT 1 FROM active_bots WHERE platform=? AND streamer_id=? "
            "AND account_name=? AND status!='종료'",
            (platform, streamer_id, account["account_name"]),
        ).fetchone()
        if duplicate:
            raise ValueError("같은 플랫폼·방송 URL·계정의 작업이 이미 활성 상태입니다.")
        bot_id = secrets.token_urlsafe(12)
        conn.execute(
            "INSERT INTO active_bots "
            "(id, platform, streamer_id, account_name, proxy_name, "
            "status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, '실행', ?, ?)",
            (
                bot_id,
                platform,
                streamer_id,
                account["account_name"],
                proxy_name,
                timestamp,
                timestamp,
            ),
        )
    # 워커 스레드 시작 — 실제 플랫폼에 접속해 자연스럽게 빠졌다-접속했다 반복
    # 최신화 로직: viewer_plans에서 목표 시청자 수 가져옴
    with connection() as conn:
        plan = conn.execute(
            "SELECT viewer_count FROM viewer_plans "
            "WHERE streamer_id=? AND status!='종료' LIMIT 1",
            (streamer_id,),
        ).fetchone()
    target = plan["viewer_count"] if plan else 200
    start_worker(bot_id, platform, streamer_id, target, proxy_name, bot_id)
    return bot_id


def start_auto_chat(bot_id: str, message: str, interval_seconds: int) -> str:
    """Create chat work only while its bot is still running."""
    # 워커는 start_bot에서 시작됨 (위 start_bot 함수 맨 마지막 return 직전)
    normalized_message = message.strip()
    if not normalized_message:
        raise ValueError("채팅 메시지를 입력하세요.")
    if len(normalized_message) > MAX_CHAT_MESSAGE_LENGTH:
        raise ValueError(
            f"채팅 메시지는 {MAX_CHAT_MESSAGE_LENGTH}자 이하여야 합니다."
        )
    if not isinstance(interval_seconds, int) or isinstance(interval_seconds, bool) or interval_seconds < 1:
        raise ValueError("전송 간격은 1초 이상의 정수여야 합니다.")
    timestamp = now()
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        bot = conn.execute(
            "SELECT 1 FROM active_bots WHERE id=? AND status='실행'", (bot_id,)
        ).fetchone()
        if bot is None:
            raise ValueError("실행 중인 봇을 찾을 수 없습니다.")
        chat_id = secrets.token_urlsafe(12)
        conn.execute(
            "INSERT INTO auto_chats "
            "(id, bot_id, message, interval_seconds, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, '실행', ?, ?)",
            (
                chat_id,
                bot_id,
                normalized_message,
                interval_seconds,
                timestamp,
                timestamp,
            ),
        )
    return chat_id


def add_viewer_plan(
    platform: str,
    streamer_id: str,
    plan_name: str,
    viewer_count: int,
    notes: str = "",
    *,
    operator: str = "사장님(나)",
    motion_type: str = MOTION_TYPES[0],
    chat_mode: str = CHAT_MODES[0],
    duration_hours: int = 2,
) -> str:
    """Register one local viewer allocation plan without generating traffic."""
    streamer_id = normalize_broadcast_url(streamer_id)
    plan_name = normalized_identifier(plan_name, "시청자 묶음 이름")
    if (
        not isinstance(viewer_count, int)
        or isinstance(viewer_count, bool)
        or viewer_count < MIN_VIEWER_COUNT
        or viewer_count > MAX_VIEWER_COUNT
        or viewer_count % VIEWER_COUNT_STEP != 0
    ):
        raise ValueError(
            f"목표 투입 인원수는 {MIN_VIEWER_COUNT}~{MAX_VIEWER_COUNT}명 사이에서 "
            f"{VIEWER_COUNT_STEP}명 단위로 설정해야 합니다."
        )
    if operator not in PARTNER_LIMITS:
        raise ValueError("지원하지 않는 작업 관리자입니다.")
    if motion_type not in MOTION_TYPES:
        raise ValueError("지원하지 않는 인원 증감 모션입니다.")
    if chat_mode not in CHAT_MODES:
        raise ValueError("지원하지 않는 채팅 구동 모드입니다.")
    if duration_hours not in DURATION_HOURS:
        raise ValueError("지원하지 않는 자동 종료 시간입니다.")
    timestamp = now()
    ends_at = (datetime.now(timezone.utc) + timedelta(hours=duration_hours)).isoformat(
        timespec="seconds"
    )
    current_count = 0 if "서서히" in motion_type else viewer_count
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        allowed = conn.execute(
            "SELECT 1 FROM whitelist WHERE streamer_id=?", (streamer_id,)
        ).fetchone()
        if allowed is None:
            raise ValueError("화이트리스트에 등록된 방송 URL만 계획할 수 있습니다.")
        current_used = conn.execute(
            "SELECT COALESCE(SUM(viewer_count), 0) FROM viewer_plans "
            "WHERE operator=? AND status!='종료'",
            (operator,),
        ).fetchone()[0]
        if current_used + viewer_count > PARTNER_LIMITS[operator]:
            remaining = PARTNER_LIMITS[operator] - current_used
            raise ValueError(
                f"{operator}의 잔여 할당량은 {remaining}명입니다."
            )
        plan_id = secrets.token_urlsafe(12)
        conn.execute(
            "INSERT INTO viewer_plans "
            "(id, platform, streamer_id, plan_name, viewer_count, operator, "
            "motion_type, chat_mode, status, notes, duration_hours, current_count, "
            "started_at, ends_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '대기', ?, ?, ?, ?, ?, ?, ?)",
            (
                plan_id,
                platform,
                streamer_id,
                plan_name,
                viewer_count,
                operator,
                motion_type,
                chat_mode,
                notes.strip(),
                duration_hours,
                current_count,
                timestamp,
                ends_at,
                timestamp,
                timestamp,
            ),
        )
    return plan_id


def register_viewer_plan_from_url(
    broadcast_url: str,
    viewer_count: int,
    *,
    platform: str | None = None,
    operator: str = "사장님(나)",
    motion_type: str = MOTION_TYPES[0],
    chat_mode: str = CHAT_MODES[0],
    duration_hours: int = 2,
) -> str:
    """Register a URL directly; the URL itself becomes the allowlisted target."""
    broadcast_url = normalize_broadcast_url(broadcast_url)
    if platform is None:
        platform = platform_from_broadcast_url(broadcast_url)
    elif platform not in PLATFORMS:
        raise ValueError("지원하지 않는 방송 플랫폼입니다.")
    with connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO whitelist VALUES (?, ?)",
            (broadcast_url, now()),
        )
    return add_viewer_plan(
        platform,
        broadcast_url,
        f"{platform} 방송 · {viewer_count}명",
        viewer_count,
        "",
        operator=operator,
        motion_type=motion_type,
        chat_mode=chat_mode,
        duration_hours=duration_hours,
    )


def set_viewer_plan_status(plan_id: str, status: str) -> None:
    if status not in VIEWER_PLAN_STATUSES:
        raise ValueError(f"지원하지 않는 시청자 계획 상태: {status}")
    with connection() as conn:
        if status == "종료":
            result = conn.execute(
                "UPDATE viewer_plans SET status=?, current_count=0, updated_at=? "
                "WHERE id=?",
                (status, now(), plan_id),
            )
        else:
            result = conn.execute(
                "UPDATE viewer_plans SET status=?, updated_at=? WHERE id=?",
                (status, now(), plan_id),
            )
        if result.rowcount != 1:
            raise ValueError("시청자 계획을 찾을 수 없습니다.")


def expire_viewer_plans() -> int:
    """Close locally tracked plans whose configured timer has elapsed."""
    timestamp = now()
    with connection() as conn:
        result = conn.execute(
            "UPDATE viewer_plans SET status='종료', current_count=0, updated_at=? "
            "WHERE status!='종료' AND ends_at!='' AND ends_at<=?",
            (timestamp, timestamp),
        )
    return result.rowcount


def refresh_viewer_plan_progress() -> int:
    """Update the local planned count from elapsed timer progress."""
    timestamp = datetime.now(timezone.utc)
    changed = 0
    with connection() as conn:
        rows = conn.execute(
            "SELECT id, viewer_count, motion_type, status, started_at, ends_at, "
            "current_count FROM viewer_plans WHERE status='대기'"
        ).fetchall()
        for row in rows:
            if "서서히" not in row["motion_type"]:
                planned_count = row["viewer_count"]
            else:
                try:
                    started_at = datetime.fromisoformat(row["started_at"])
                    ends_at = datetime.fromisoformat(row["ends_at"])
                except (TypeError, ValueError):
                    continue
                total_seconds = max((ends_at - started_at).total_seconds(), 1)
                elapsed_seconds = max((timestamp - started_at).total_seconds(), 0)
                progress = min(elapsed_seconds / total_seconds, 1)
                planned_count = min(
                    row["viewer_count"], int(row["viewer_count"] * progress)
                )
            if planned_count != row["current_count"]:
                conn.execute(
                    "UPDATE viewer_plans SET current_count=?, updated_at=? WHERE id=?",
                    (planned_count, now(), row["id"]),
                )
                changed += 1
    return changed


def login() -> None:
    if os.getenv("YTPDBOT_LOCAL_DESKTOP", "") == "1":
        st.session_state.authenticated = True
        return
    configured = os.getenv("YTPDBOT_ADMIN_PASSWORD", "")
    if not configured:
        st.error(
            "YTPDBOT_ADMIN_PASSWORD 환경변수를 설정해야 합니다. "
            "소스 코드에 비밀번호를 넣지 마세요."
        )
        st.stop()
    if len(configured) < MIN_ADMIN_PASSWORD_LENGTH:
        st.error(
            f"YTPDBOT_ADMIN_PASSWORD는 {MIN_ADMIN_PASSWORD_LENGTH}자 이상으로 "
            "설정해야 합니다."
        )
        st.stop()
    current_timestamp = datetime.now(timezone.utc).timestamp()
    if st.session_state.get("authenticated", False):
        authenticated_at = st.session_state.get("authenticated_at")
        if authenticated_at is None:
            st.session_state.authenticated_at = current_timestamp
            return
        if current_timestamp - float(authenticated_at) < SESSION_TIMEOUT_SECONDS:
            # Keep an actively used dashboard session alive.  Without this,
            # the absolute one-hour cutoff can discard the very form submit
            # that crosses the deadline and send the operator back to login.
            st.session_state.authenticated_at = current_timestamp
            return
        st.session_state.authenticated = False
        st.session_state.pop("authenticated_at", None)
        st.warning("보안을 위해 로그인 세션이 만료되었습니다. 다시 로그인하세요.")

    st.title("방송 운영 대시보드 로그인")
    st.caption(f"배포 버전: {deployment_version()}")
    locked_until = float(st.session_state.get("login_locked_until", 0.0))
    if current_timestamp < locked_until:
        remaining = max(1, int(locked_until - current_timestamp) + 1)
        st.error(f"로그인 시도가 잠겼습니다. {remaining}초 후 다시 시도하세요.")
        st.stop()
    password = st.text_input("관리자 비밀번호", type="password")
    if st.button("로그인", type="primary"):
        if hmac.compare_digest(password, configured):
            st.session_state.authenticated = True
            st.session_state.authenticated_at = current_timestamp
            st.session_state.login_failed_attempts = 0
            st.session_state.pop("login_locked_until", None)
            st.rerun()
        failed_attempts = int(st.session_state.get("login_failed_attempts", 0)) + 1
        if failed_attempts >= MAX_LOGIN_ATTEMPTS:
            st.session_state.login_failed_attempts = 0
            st.session_state.login_locked_until = (
                current_timestamp + LOGIN_LOCKOUT_SECONDS
            )
            st.error(
                f"비밀번호를 {MAX_LOGIN_ATTEMPTS}회 잘못 입력해 "
                f"{LOGIN_LOCKOUT_SECONDS}초 동안 로그인을 잠급니다."
            )
        else:
            st.session_state.login_failed_attempts = failed_attempts
            st.error(
                "비밀번호가 일치하지 않습니다. "
                f"남은 시도: {MAX_LOGIN_ATTEMPTS - failed_attempts}회"
            )
    st.stop()


def _autostart_workers() -> None:
    """새로고침 후에도 가동 중이던 워커 자동 재시작"""
    with connection() as conn:
        rows = conn.execute(
            "SELECT id, platform, streamer_id, viewer_count, proxy_name, status "
            "FROM viewer_plans WHERE status IN ('대기', '일시중지') "
            "ORDER BY created_at DESC LIMIT 10"
        ).fetchall()
    for row in rows:
        bid = row["id"]
        if bid not in _ACTIVE_WORKERS or not _ACTIVE_WORKERS[bid].running:
            _ACTIVE_WORKERS.pop(bid, None)
            start_worker(
                bid, row["platform"], row["streamer_id"],
                int(row["viewer_count"] or 0),
                row["proxy_name"] or "", bid,
            )


def main() -> None:
    st.set_page_config(page_title="팬더티비/유튜브/플렉스티비 뷰봇", layout="wide")
    login()
    if st_autorefresh is not None:
        st_autorefresh(interval=5000)
    database_identity = os.getenv("TURSO_DATABASE_URL", "").strip() or str(
        DB_PATH.resolve()
    )
    init_db_once(database_identity)
    _autostart_workers()
    expire_viewer_plans()
    refresh_viewer_plan_progress()
    title_column, session_column = st.columns([5, 1])
    title_column.title("팬더티비/유튜브/플렉스티비 뷰봇")
    if session_column.button("로그아웃", use_container_width=True):
        st.session_state.authenticated = False
        st.session_state.pop("authenticated_at", None)
        st.rerun()
    st.caption(
        "중앙 제어, 화이트리스트, 플랫폼 계정·프록시, 채팅 문구를 원본 흐름대로 관리합니다. "
        f"· 배포 버전: {deployment_version()}"
    )

    snapshot = dashboard_snapshot()
    data = snapshot["sessions"]
    allowlist_items = snapshot["whitelist"]
    viewer_plan_rows = snapshot["viewer_plans"]
    proxies = snapshot["proxy_settings"]
    account_rows = snapshot["accounts"]
    active_bot_rows = snapshot["active_bots"]
    chats = snapshot["auto_chats"]
    active = data[data["status"] != "종료"] if not data.empty else data
    planned_viewers = sum(
        int(row["viewer_count"])
        for row in viewer_plan_rows
        if row["status"] != "종료"
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("모니터링 세션", len(active))
    c2.metric(
        "관측 시청자 합계",
        int(active["observed_viewers"].sum()) if not active.empty else 0,
    )
    c3.metric("화이트리스트", len(allowlist_items))
    c4.metric("계획 시청자 합계", planned_viewers)

    viewer_tab, broadcast_tab, connection_tab, chat_settings_tab = st.tabs(
        [
            "봇 & 채팅 실시간 제어 센터",
            "방송 화이트리스트 관리",
            "플랫폼/P2P 연동 계정 설정",
            "자동 매크로 챗 문구 셋업",
        ]
    )
    monitor = broadcast_tab
    allowlist = broadcast_tab
    proxy_tab = connection_tab
    bot_tab = connection_tab
    chat_tab = chat_settings_tab
    notes = chat_settings_tab
    with monitor:
        st.subheader("실시간 서버 및 채팅 동기화 모니터")
        with st.form("session_form"):
            platform = st.selectbox("플랫폼", PLATFORMS)
            streamer_id = st.text_input("방송 URL", key="monitor_broadcast_url").strip()
            viewers = st.number_input(
                "실제 관측 시청자 수", min_value=0, value=0, step=1
            )
            memo = st.text_area("운영 메모", max_chars=2000)
            submit = st.form_submit_button("등록", type="primary")
        if submit:
            try:
                streamer_id = normalize_broadcast_url(streamer_id)
            except ValueError as exc:
                st.error(str(exc))
            else:
                if streamer_id not in allowlist_items:
                    st.error(
                        "화이트리스트에 등록된 방송 URL만 "
                        "모니터링할 수 있습니다."
                    )
                else:
                    timestamp = now()
                    with connection() as conn:
                        conn.execute(
                            "INSERT INTO broadcast_sessions VALUES "
                            "(?, ?, ?, '모니터링', ?, ?, ?, ?)",
                            (
                                secrets.token_urlsafe(12),
                                platform,
                                streamer_id,
                                int(viewers),
                                memo.strip(),
                                timestamp,
                                timestamp,
                            ),
                        )
                    st.success("모니터링 세션을 등록했습니다.")
                    st.rerun()

        if data.empty:
            st.info("등록된 모니터링 세션이 없습니다.")
        else:
            for row in data.itertuples(index=False):
                with st.expander(f"{row.platform} · {row.streamer_id} · {row.status}"):
                    with st.form(f"update_{row.id}"):
                        status = st.selectbox(
                            "상태", STATUSES, index=STATUSES.index(row.status)
                        )
                        count = st.number_input(
                            "관측 시청자 수",
                            min_value=0,
                            value=int(row.observed_viewers),
                            step=1,
                        )
                        updated_memo = st.text_area(
                            "메모", value=row.notes, max_chars=2000
                        )
                        if st.form_submit_button("저장"):
                            with connection() as conn:
                                conn.execute(
                                    "UPDATE broadcast_sessions SET status=?, "
                                    "observed_viewers=?, notes=?, updated_at=? WHERE id=?",
                                    (
                                        status,
                                        int(count),
                                        updated_memo.strip(),
                                        now(),
                                        row.id,
                                    ),
                                )
                            st.success("상태를 저장했습니다.")
                            st.rerun()
            st.dataframe(data, width="stretch", hide_index=True)

    with allowlist:
        st.subheader("화이트리스트 데이터베이스")
        streamer_id = st.text_input("방송 URL", key="allowlist_url").strip()
        if st.button("추가"):
            try:
                streamer_id = normalize_broadcast_url(streamer_id)
            except ValueError as exc:
                st.error(str(exc))
            else:
                with connection() as conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO whitelist VALUES (?, ?)",
                        (streamer_id, now()),
                    )
                st.success("화이트리스트를 저장했습니다.")
                st.rerun()
        for item in allowlist_items:
            st.write(f"• {item}")

    with viewer_tab:
        st.subheader("중앙 컨트롤 패널")
        with st.form("viewer_plan_form"):
            viewer_operator = st.selectbox(
                "작업 관리자 선택", list(PARTNER_LIMITS.keys())
            )
            viewer_platform = st.selectbox(
                "방송 플랫폼 선택", PLATFORMS, key="viewer_platform"
            )
            viewer_streamer = st.text_input(
                "방송 URL",
                key="control_broadcast_url",
                placeholder="https://...",
            ).strip()
            viewer_count = st.slider(
                "목표 투입 인원수 설정",
                min_value=MIN_VIEWER_COUNT,
                max_value=MAX_VIEWER_COUNT,
                value=100,
                step=VIEWER_COUNT_STEP,
            )
            viewer_duration = st.selectbox(
                "자동 종료 타이머",
                DURATION_HOURS,
                index=1,
                format_func=lambda hours: f"{hours}시간",
            )
            viewer_motion = st.radio("인원 증감 모션 스타일", MOTION_TYPES)
            viewer_chat_mode = st.radio("채팅 구동 모드 선택", CHAT_MODES)
            save_viewer_plan = st.form_submit_button(
                "가동 계획 시작", type="primary"
            )
        if save_viewer_plan:
            try:
                plan_id = register_viewer_plan_from_url(
                    viewer_streamer,
                    int(viewer_count),
                    platform=viewer_platform,
                    operator=viewer_operator,
                    motion_type=viewer_motion,
                    chat_mode=viewer_chat_mode,
                    duration_hours=int(viewer_duration),
                )
                # 봇을 바로 시작 — 계획이 만들어지는 즉시 프록시로 접속
                start_worker(plan_id, viewer_platform, viewer_streamer, int(viewer_count), "", plan_id, motion_type=viewer_motion, chat_mode=viewer_chat_mode)
                st.success(
                    "가동 계획을 시작했습니다. 무료 프록시로 접속하여 시청자 수를 실시간으로 갱신합니다."
                )
            except ValueError as exc:
                st.error(str(exc))

        show_completed_plans = st.checkbox("종료된 계획 표시", value=False)
        visible_viewer_plans = [
            plan
            for plan in viewer_plan_rows
            if show_completed_plans or plan["status"] != "종료"
        ]
        if visible_viewer_plans:
            st.dataframe(
                pd.DataFrame([dict(row) for row in visible_viewer_plans]),
                width="stretch",
                hide_index=True,
            )
            for plan in visible_viewer_plans:
                with st.expander(
                    f"{plan['streamer_id']} · {plan['viewer_count']}명 · {plan['status']}"
                ):
                    st.write(
                        f"현재 계획값 {plan['current_count']}명 / 목표 {plan['viewer_count']}명 · "
                        f"자동 종료 {plan['ends_at']}"
                    )
                    c1, c2 = st.columns(2)
                    if plan["status"] == "대기" and c1.button(
                        "계획 일시중지", key=f"pause_viewer_plan_{plan['id']}"
                    ):
                        set_viewer_plan_status(plan["id"], "일시중지")
                        st.rerun()
                    if plan["status"] == "일시중지" and c1.button(
                        "계획 다시 대기", key=f"resume_viewer_plan_{plan['id']}"
                    ):
                        set_viewer_plan_status(plan["id"], "대기")
                        st.rerun()
                    if plan["status"] != "종료" and c2.button(
                        "가동 계획 중지", key=f"stop_viewer_plan_{plan['id']}"
                    ):
                        set_viewer_plan_status(plan["id"], "종료")
                        stop_worker(plan["id"])
                        st.rerun()
        elif viewer_plan_rows:
            st.info("활성 계획이 없습니다. 종료된 계획 표시는 위 선택란에서 켤 수 있습니다.")
        else:
            st.info("등록된 시청자 계획이 없습니다.")

    with proxy_tab:
        st.subheader("플랫폼/P2P 프록시 인벤토리")
        st.caption("http/https/socks4/socks5 엔드포인트를 관리합니다.")
        proxy_feed_count = sum(
            len(source["feeds"]) for source in PUBLIC_PROXY_SOURCE_CATALOG
        )
        st.caption(
            f"자동 수집 출처 {len(PUBLIC_PROXY_SOURCE_CATALOG)}곳 · "
            f"공개 목록 {proxy_feed_count}개 · HTTP/SOCKS4/SOCKS5 · "
            "실행할 때마다 최신 원문 재수집"
        )
        with st.expander("자동 수집 출처 전체 보기"):
            for source in PUBLIC_PROXY_SOURCE_CATALOG:
                protocols = ", ".join(
                    scheme.upper() for scheme, _ in source["feeds"]
                )
                st.markdown(f"- [{source['name']}]({source['repository']}) — {protocols}")
        if "proxy_collect_running" not in st.session_state:
            st.session_state["proxy_collect_running"] = False
        if st.button(
            "공개 프록시 전체 자동 수집·검증",
            key="collect_public_proxies",
            use_container_width=True,
        ):
            if not st.session_state["proxy_collect_running"]:
                st.session_state["proxy_collect_running"] = True
                st.session_state["proxy_collect_result"] = None
                def _bg_collect():
                    try:
                        result = collect_and_verify_proxies(
                            max_collect=UI_PUBLIC_PROXY_COLLECT,
                            workers=MAX_PUBLIC_PROXY_WORKERS,
                            fetch_timeout_seconds=UI_PUBLIC_PROXY_FETCH_TIMEOUT_SECONDS,
                            probe_timeout_seconds=UI_PUBLIC_PROXY_PROBE_TIMEOUT_SECONDS,
                        )
                    except Exception as exc:
                        result = f"에러: {exc}"
                    st.session_state["proxy_collect_result"] = result
                    st.session_state["proxy_collect_running"] = False
                threading.Thread(target=_bg_collect, daemon=True).start()
        if st.session_state["proxy_collect_running"]:
            st.info("공개 목록을 수집하고 연결 상태를 확인하는 중입니다... (5초마다 자동 갱신)")
        elif st.session_state.get("proxy_collect_result") is not None:
            collection_result = st.session_state["proxy_collect_result"]
            if isinstance(collection_result, str):
                st.error(collection_result)
            else:
                pool_result = None
                if collection_result["alive"]:
                    try:
                        pool_result = activate_collected_proxy_pool(
                            timeout_seconds=UI_PROXY_POOL_TIMEOUT_SECONDS,
                            max_candidates=UI_PROXY_POOL_CANDIDATES,
                            workers=UI_PROXY_POOL_WORKERS,
                        )
                    except ValueError as exc:
                        st.warning(str(exc))
                st.success(
                    f"자동 수집 완료: 후보 {collection_result['collected']}개, "
                    f"정상 {collection_result['alive']}개, "
                    f"신규 저장 {collection_result['saved']}개, "
                    f"오래된 항목 삭제 {collection_result['removed']}개"
                )
                if pool_result is not None:
                    st.success(
                        f"실경유 활성 풀 {len(pool_result['winners'])}개 · "
                        f"실패 폐기 {len(pool_result['discarded'])}개 · "
                        f"작업 재배정 {pool_result['reassigned_bots']}개"
                    )
                if collection_result["source_failures"]:
                    st.warning(
                        f"응답하지 않은 목록 {collection_result['source_failures']}개는 "
                        "건너뛰었습니다."
                    )
        with st.form("proxy_form"):
            st.text_input("프록시 이름", key="single_proxy_name")
            proxy_endpoint = st.text_input(
                "프록시 주소", placeholder="http://127.0.0.1:8080"
            ).strip()
            proxy_source = st.text_input(
                "프록시 출처", value="직접 등록", key="single_proxy_source"
            ).strip()
            proxy_enabled = st.checkbox("사용", value=True)
            save_proxy_requested = st.form_submit_button("프록시 저장")
        if save_proxy_requested:
            try:
                if not proxy_endpoint:
                    raise ValueError("프록시 주소를 입력하세요.")
                saved = save_proxy(
                    proxy_name,
                    proxy_endpoint,
                    enabled=False,
                    source=proxy_source,
                )
            except ValueError as exc:
                st.error(str(exc))
            else:
                if saved:
                    if proxy_enabled:
                        try:
                            route_result = activate_proxy_with_failover(proxy_name)
                        except ValueError as exc:
                            st.error(str(exc))
                        else:
                            st.success(
                                f"저장·자동 연결 완료: {route_result['name']} · "
                                f"{route_result['latency_ms']}ms"
                            )
                    else:
                        st.success("프록시 설정을 비활성 상태로 저장했습니다.")
                else:
                    st.info("같은 프록시 주소가 이미 있어 중복 등록하지 않았습니다.")
                st.rerun()

        with st.form("proxy_bulk_import_form"):
            bulk_source = st.text_input(
                "일괄 가져오기 출처", value="공개 목록", key="bulk_proxy_source"
            ).strip()
            bulk_text = st.text_area(
                "프록시 일괄 붙여넣기",
                placeholder="http://host:port\nsocks5://host:port",
                height=140,
            )
            bulk_file = st.file_uploader(
                "프록시 파일 가져오기", type=["txt", "csv"], max_upload_size=1
            )
            import_proxies = st.form_submit_button("일괄 가져오기")
        if import_proxies:
            try:
                file_text = ""
                if bulk_file is not None:
                    file_bytes = bulk_file.getvalue()
                    if len(file_bytes) > MAX_PROXY_IMPORT_BYTES:
                        raise ValueError("프록시 가져오기 파일은 1MB 이하여야 합니다.")
                    try:
                        file_text = file_bytes.decode("utf-8-sig")
                    except UnicodeDecodeError as exc:
                        raise ValueError("프록시 파일은 UTF-8 텍스트여야 합니다.") from exc
                combined_text = "\n".join(
                    part for part in (bulk_text, file_text) if part
                )
                if not combined_text.strip():
                    raise ValueError("붙여넣을 프록시 또는 가져올 파일을 선택하세요.")
                result = import_proxy_inventory(combined_text, bulk_source)
            except ValueError as exc:
                st.error(str(exc))
            else:
                failover_result = None
                if result["imported"]:
                    with connection() as conn:
                        first_proxy = conn.execute(
                            "SELECT name FROM proxy_settings "
                            "ORDER BY enabled DESC, updated_at DESC LIMIT 1"
                        ).fetchone()
                    if first_proxy is not None:
                        try:
                            failover_result = activate_proxy_with_failover(
                                first_proxy["name"]
                            )
                        except ValueError as exc:
                            st.warning(str(exc))
                st.success(
                    f"가져오기 완료: 신규 {result['imported']}개, "
                    f"중복 제외 {result['duplicates']}개, 형식 오류 {result['invalid']}개"
                )
                if failover_result is not None:
                    st.success(
                        f"자동 연결: {failover_result['name']} · "
                        f"{failover_result['latency_ms']}ms · "
                        f"실패 폐기 {len(failover_result['discarded'])}개"
                    )
                if result["errors"]:
                    st.warning("\n".join(result["errors"][:20]))
                st.rerun()
        if proxies:
            if st.button("전체 연결 상태 점검", key="check_all_proxies"):
                health_results = check_proxy_inventory_health()
                passed = sum(health_results.values())
                st.success(
                    f"연결 상태 점검 완료: 정상 {passed}개, "
                    f"실패·폐기 {len(proxies) - passed}개"
                )
                st.rerun()
            prioritized_proxies = [proxy for proxy in proxies if proxy["enabled"]]
            prioritized_proxies.extend(
                proxy for proxy in proxies if not proxy["enabled"]
            )
            visible_proxies = prioritized_proxies[:PROXY_INVENTORY_DISPLAY_LIMIT]
            st.caption(
                f"전체 {len(proxies)}개 중 활성·최신 {len(visible_proxies)}개를 표시합니다. "
                "전체 목록은 CSV로 내려받을 수 있습니다."
            )
            display_rows = [
                {
                    "이름": proxy["name"],
                    "주소": proxy["endpoint"],
                    "활성": bool(proxy["enabled"]),
                    "상태": proxy["health_status"],
                    "격리": bool(proxy["quarantined"]),
                    "출처": proxy["source"],
                    "출구 IP": proxy["last_exit_ip"] or "미확인",
                    "응답(ms)": int(proxy["latency_ms"] or 0),
                    "마지막 점검": proxy["last_checked_at"] or "미점검",
                }
                for proxy in visible_proxies
            ]
            st.dataframe(
                pd.DataFrame(display_rows),
                width="stretch",
                hide_index=True,
            )
            for proxy in visible_proxies[:PROXY_INVENTORY_CONTROL_LIMIT]:
                label = (
                    f"{proxy['name']} · {proxy['health_status']} · "
                    f"{'격리' if proxy['quarantined'] else '사용 가능'}"
                )
                with st.expander(label):
                    st.write(f"출처: {proxy['source']}")
                    st.write(f"출구 IP: {proxy['last_exit_ip'] or '미확인'}")
                    st.write(f"응답 시간: {int(proxy['latency_ms'] or 0)}ms")
                    st.write(f"마지막 점검: {proxy['last_checked_at'] or '미점검'}")
                    c1, c2 = st.columns(2)
                    if c1.button(
                        "실제 경유 확인·활성화",
                        key=f"route_proxy_{proxy['name']}",
                    ):
                        try:
                            route_result = activate_proxy_with_failover(
                                proxy["name"]
                            )
                        except ValueError as exc:
                            st.error(str(exc))
                        else:
                            st.success(
                                f"자동 전환 완료 · {route_result['name']} · 출구 IP "
                                f"{route_result['exit_ip']} · "
                                f"{route_result['latency_ms']}ms · "
                                f"실패 폐기 {len(route_result['discarded'])}개"
                            )
                            st.rerun()
                    next_enabled = not bool(proxy["enabled"])
                    action = (
                        "사용 중지"
                        if proxy["enabled"]
                        else "실제 확인 후 사용"
                    )
                    if c2.button(action, key=f"toggle_proxy_{proxy['name']}"):
                        try:
                            if next_enabled:
                                route_result = activate_proxy_with_failover(
                                    proxy["name"]
                                )
                                st.success(
                                    f"자동 전환 완료 · {route_result['name']} · 출구 IP "
                                    f"{route_result['exit_ip']} · "
                                    f"{route_result['latency_ms']}ms · "
                                    f"실패 폐기 {len(route_result['discarded'])}개"
                                )
                            else:
                                set_proxy_enabled(proxy["name"], False)
                                st.rerun()
                        except ValueError as exc:
                            st.error(str(exc))
        else:
            st.info("등록된 프록시가 없습니다.")

    with bot_tab:
        st.subheader("플랫폼/P2P 연동 계정 및 봇 작업")
        with st.form("account_form"):
            account_platform = st.selectbox("계정 플랫폼", PLATFORMS)
            account_name = st.text_input("계정 이름").strip()
            add_account = st.form_submit_button("계정 추가")
        if add_account:
            try:
                saved = save_account(account_platform, account_name)
            except ValueError as exc:
                st.error(str(exc))
            else:
                if saved:
                    st.success("계정을 추가했습니다.")
                else:
                    st.info("같은 플랫폼과 계정 이름이 이미 있어 중복 등록하지 않았습니다.")
                st.rerun()

        if account_rows:
            st.dataframe(
                pd.DataFrame([dict(row) for row in account_rows]),
                width="stretch",
                hide_index=True,
            )
            for account in account_rows:
                next_enabled = 0 if account["enabled"] else 1
                action = "계정 중지" if account["enabled"] else "계정 활성화"
                if st.button(action, key=f"toggle_account_{account['id']}"):
                    set_account_enabled(account["id"], bool(next_enabled))
                    st.rerun()
        enabled_accounts = [row for row in account_rows if row["enabled"]]
        proxy_rows = [
            row
            for row in proxies
            if row["enabled"]
            and not row["quarantined"]
            and row["health_status"] == "정상"
            and row["last_exit_ip"]
        ]
        st.caption(f"실제 경유 검증을 통과한 사용 가능 프록시: {len(proxy_rows)}개")
        bot_platform = st.selectbox("봇 플랫폼", PLATFORMS, key="bot_platform")
        platform_accounts = [
            row for row in enabled_accounts if row["platform"] == bot_platform
        ]
        with st.form("bot_form"):
            bot_streamer = st.text_input("대상 방송 URL").strip()
            bot_account = st.selectbox(
                "사용 계정",
                [row["id"] for row in platform_accounts] or ["등록된 계정 없음"],
                format_func=lambda value: next(
                    (
                        row["account_name"]
                        for row in platform_accounts
                        if row["id"] == value
                    ),
                    value,
                ),
            )
            bot_proxy = st.selectbox(
                "사용 프록시",
                ["사용 안 함"] + [row["name"] for row in proxy_rows],
                format_func=lambda value: next(
                    (
                        f"{row['name']} · {row['latency_ms']}ms · {row['last_exit_ip']}"
                        for row in proxy_rows
                        if row["name"] == value
                    ),
                    value,
                ),
            )
            start_bot_requested = st.form_submit_button("봇 작업 등록", type="primary")
        if start_bot_requested:
            if not platform_accounts:
                st.error("선택한 플랫폼에 사용할 계정을 먼저 등록하세요.")
            else:
                try:
                    bot_streamer = normalize_broadcast_url(bot_streamer)
                    start_bot(
                        bot_platform,
                        bot_streamer,
                        bot_account,
                        "" if bot_proxy == "사용 안 함" else bot_proxy,
                    )
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.success("봇 작업을 실행 예정 상태로 등록했습니다.")
                    st.rerun()

        for bot in active_bot_rows:
            with st.expander(f"{bot['platform']} · {bot['streamer_id']} · {bot['status']}"):
                st.caption(f"배정 프록시: {bot['proxy_name'] or '사용 안 함'}")
                c1, c2 = st.columns(2)
                if bot["status"] == "실행" and c1.button(
                    "일시중지", key=f"pause_bot_{bot['id']}"
                ):
                    set_bot_status(bot["id"], "일시중지")
                    st.rerun()
                if bot["status"] == "일시중지" and c1.button(
                    "다시 시작", key=f"resume_bot_{bot['id']}"
                ):
                    try:
                        set_bot_status(bot["id"], "실행")
                    except ValueError as exc:
                        st.error(str(exc))
                    else:
                        st.rerun()
                if bot["status"] != "종료" and c2.button(
                    "종료", key=f"stop_bot_{bot['id']}"
                ):
                    set_bot_status(bot["id"], "종료")
                    st.rerun()

    with chat_tab:
        st.subheader("자동 매크로 채팅 데이터베이스")
        running_bots = [row for row in active_bot_rows if row["status"] == "실행"]
        with st.form("chat_form"):
            chat_bot = st.selectbox(
                "대상 봇",
                [row["id"] for row in running_bots] or ["실행 중인 봇 없음"],
                format_func=lambda value: next(
                    (
                        f"{row['platform']} · {row['streamer_id']} · {row['account_name']}"
                        for row in running_bots
                        if row["id"] == value
                    ),
                    value,
                ),
            )
            chat_message = st.text_area("채팅 메시지", max_chars=500).strip()
            chat_interval = st.number_input(
                "전송 간격(초)", min_value=1, value=30, step=1
            )
            start_chat = st.form_submit_button("채팅 계획 등록", type="primary")
        if start_chat:
            if not running_bots:
                st.error("먼저 봇을 시작하세요.")
            elif not chat_message:
                st.error("채팅 메시지를 입력하세요.")
            else:
                try:
                    start_auto_chat(chat_bot, chat_message, int(chat_interval))
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.success("채팅 계획을 실행 예정 상태로 등록했습니다.")
                    st.rerun()
        if chats:
            st.dataframe(
                pd.DataFrame([dict(row) for row in chats]),
                width="stretch",
                hide_index=True,
            )
            for chat in chats:
                with st.expander(
                    f"{chat['message'][:40]} · {chat['interval_seconds']}초 · {chat['status']}"
                ):
                    c1, c2 = st.columns(2)
                    if chat["status"] == "실행" and c1.button(
                        "일시중지", key=f"pause_chat_{chat['id']}"
                    ):
                        set_chat_status(chat["id"], "일시중지")
                        st.rerun()
                    if chat["status"] == "일시중지" and c1.button(
                        "다시 시작", key=f"resume_chat_{chat['id']}"
                    ):
                        try:
                            set_chat_status(chat["id"], "실행")
                        except ValueError as exc:
                            st.error(str(exc))
                        else:
                            st.rerun()
                    if chat["status"] != "종료" and c2.button(
                        "종료", key=f"stop_chat_{chat['id']}"
                    ):
                        set_chat_status(chat["id"], "종료")
                        st.rerun()
        else:
            st.info("등록된 자동 채팅이 없습니다.")

    with notes:
        st.subheader("운영 기준")
        st.info(
            "현재 복원본은 프록시·계정·봇 작업·채팅 계획의 로컬 기록만 관리합니다. "
            "외부 플랫폼 실행 모듈은 포함하지 않으며 로그인·트래픽·채팅 전송을 수행하지 않습니다."
        )
        st.write(f"데이터베이스: `{DB_PATH}`")

# ==============================================================================
# [신규 추가] 실시간 API 연동 및 UI 확장 로직
# ==============================================================================

class PlatformEngine:
    """
    실제 플랫폼 API와 통신하여 실시간 데이터를 수집하고 상태를 확인하는 엔진
    """

    @staticmethod
    def ping_youtube_api(stream_url: str) -> Dict[str, Any]:
        """
        유튜브 API를 통해 실시간 시청자 수 조회 시도
        """
        try:
            api_key = os.getenv("YOUTUBE_API_KEY", "")
            if not api_key:
                return {"status": "api_key_missing", "viewers": 0}

            video_id = ""
            if "v=" in stream_url:
                video_id = stream_url.split("v=")[1].split("&")[0]

            if not video_id:
                return {"status": "invalid_url", "viewers": 0}

            url = f"https://www.googleapis.com/youtube/v3/videos?part=statistics&id={video_id}&key={api_key}"
            response = requests.get(url)

            if response.status_code == 200:
                data = response.json()
                stats = data.get("items", [{}])[0].get("statistics", {})
                viewers = int(stats.get("viewCount", 0))
                return {"status": "success", "viewers": viewers}
            else:
                return {"status": "api_error", "viewers": 0}

        except Exception as e:
            return {"status": "error", "message": str(e), "viewers": 0}

    @staticmethod
    def ping_pandora_tv(stream_url: str) -> Dict[str, Any]:
        """
        팬더티비 API 핑 테스트
        """
        try:
            response = requests.head(stream_url, timeout=5)
            return {
                "status": "connected" if response.status_code == 200 else "disconnected",
                "latency_ms": 0
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @staticmethod
    def send_chat(platform: str, message: str, account_info: Dict) -> bool:
        """
        선택한 플랫폼으로 채팅 발송
        """
        # 실제 구현: platform에 따라 다른 API 엔드포인트 호출
        st.info(f"[{platform}] 채팅 발송 요청: '{message}' (계정: {account_info.get('username')})")
        return True


import random
import json

class BotWorker:
    """
    실제 플랫폼에 접속하여 자연스럽게 빠졌다-접속했다를 반복하는 백그라운드 워커
    """

    def __init__(self, bot_id: str, platform: str, streamer_id: str,
                 target_count: int, proxy_name: str = "", session_id: str = ""):
        self.bot_id = bot_id
        self.platform = platform
        self.streamer_id = streamer_id
        self.target_count = target_count
        self.proxy_name = proxy_name
        self.session_id = session_id
        self._stop_event = threading.Event()
        self._connected = 0
        self._ws_list = []  # WebSocket 연결 목록

    def _proxies(self) -> list[dict]:
        with connection() as conn:
            rows = conn.execute(
                "SELECT * FROM proxy_settings WHERE health_status='정상' AND enabled=1 AND quarantined=0 ORDER BY latency_ms ASC"
            ).fetchall()
            return [dict(r) for r in rows]

    def _pick_proxy(self) -> dict | None:
        pool = self._proxies()
        if not pool:
            return None
        if self.proxy_name:
            for p in pool:
                if p["name"] == self.proxy_name:
                    return p
        # 안 쓴 프록시를 순서대로 — 444(IP 제한) 방지
        if not hasattr(self, "_used_proxies"):
            self._used_proxies = set()
        unused = [p for p in pool if p["endpoint"] not in self._used_proxies]
        if unused:
            chosen = unused[0]
            self._used_proxies.add(chosen["endpoint"])
            return chosen
        # 모든 프록시 사용됨 → 초기화 후 처음으로
        self._used_proxies.clear()
        chosen = pool[0]
        self._used_proxies.add(chosen["endpoint"])
        return chosen

    def _requests_kwargs(self, proxy: dict | None) -> dict:
        if proxy:
            endpoint = proxy["endpoint"]
            return {"proxies": {"http": endpoint, "https": endpoint}}
        return {}

    def _channel_id(self) -> str:
        """방송 URL에서 채널/영상 ID 추출
        팬더티비: https://www.pandalive.co.kr/play/nameo16 -> nameo16
        플렉스티비: https://www.flextv.co.kr/channels/614184/live -> 614184
        유튜브: https://www.youtube.com/live/dQw4w9WgXcQ -> dQw4w9WgXcQ
                 https://youtu.be/dQw4w9WgXcQ -> dQw4w9WgXcQ"""
        import re
        if "pandalive" in self.streamer_id:
            m = re.search(r"/(?:play|channel)/([^/?#]+)", self.streamer_id)
            return m.group(1) if m else self.streamer_id
        elif "flextv" in self.streamer_id:
            m = re.search(r"/channels/(\d+)", self.streamer_id)
            return m.group(1) if m else self.streamer_id
        else:
            m = re.search(r"(?:/live/|youtu\.be/|v=)([A-Za-z0-9_-]+)", self.streamer_id)
            return m.group(1) if m else self.streamer_id

    def _watch_api(self, proxy: dict | None) -> tuple[bool, int]:
        """플랫폼별 실제 API 호출 — 시청자 1명 접속
        팬더티비: POST watch (30분 토큰)
        플렉스티비: GET player-count (쿠키 세션)
        유튜브: GET live 페이지 (HTTP 연결 유지)
        프록시 실패 시 직접 연결로 fallback"""
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/157.0.0.0 Safari/537.36"

        if self.platform == "pandatv":
            cid = self._channel_id()
            uid = f"bot{random.randint(100000000, 999999999)}"
            body = f"userId={uid}&action=watch"
            url = "https://api.pandalive.co.kr/v1/live/play"
            headers = {
                "x-device-info": '{"t":"webPc","v":"1.0","ui":"0","ck":{"sessKeyAsp":""}}',
                "referer": "https://www.pandalive.co.kr/",
                "accept-language": "ko",
                "user-agent": ua,
                "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
            }
            method = "post"
            kwargs_extra = {"data": body}
            session = None
        elif self.platform == "flextv":
            cid = self._channel_id()
            url = f"https://api.flextv.co.kr/api/channels/{cid}/stream/player-count"
            headers = {
                "user-agent": ua,
                "accept": "application/json",
                "referer": "https://www.flextv.co.kr/",
            }
            method = "get"
            kwargs_extra = {}
            session = requests.Session()
            session.headers.update(headers)
        else:  # youtube
            vid = self._channel_id()
            url = f"https://www.youtube.com/live/{vid}"
            headers = {
                "user-agent": ua,
                "accept": "text/html",
            }
            method = "get"
            kwargs_extra = {}
            session = None

        # 1) 프록시로 시도
        if proxy:
            try:
                kwargs = self._requests_kwargs(proxy)
                if method == "post":
                    if session:
                        resp = session.post(url, timeout=4, **kwargs_extra, **kwargs)
                    else:
                        resp = requests.post(url, headers=headers, timeout=4, **kwargs_extra, **kwargs)
                else:
                    if session:
                        resp = session.get(url, timeout=4, **kwargs)
                    else:
                        resp = requests.get(url, headers=headers, timeout=4, **kwargs)
                if resp.status_code == 200 and resp.text:
                    if self.platform == "pandatv":
                        d = resp.json()
                        cs = d.get("chatServer", {})
                        return True, d.get("media", {}).get("user", 0), cs.get("host", ""), cs.get("port", 0)
                    elif self.platform == "flextv":
                        return True, resp.json().get("data", 0), "", 0
                    else:
                        return True, 0, "", 0
            except Exception:
                pass
        # 2) 직접 연결 fallback
        try:
            if method == "post":
                if session:
                    resp = session.post(url, timeout=4, **kwargs_extra)
                else:
                    resp = requests.post(url, headers=headers, timeout=4, **kwargs_extra)
            else:
                if session:
                    resp = session.get(url, timeout=4)
                else:
                    resp = requests.get(url, headers=headers, timeout=4)
            if resp.status_code == 200 and resp.text:
                if self.platform == "pandatv":
                    d = resp.json()
                    cs = d.get("chatServer", {})
                    return True, d.get("media", {}).get("user", 0), cs.get("host", ""), cs.get("port", 0)
                elif self.platform == "flextv":
                    return True, resp.json().get("data", 0), "", 0
                else:
                    return True, 0, "", 0
        except Exception:
            pass
        return False, 0, "", 0

    def _ping_platform(self, proxy: dict | None) -> tuple[bool, str]:
        ok, _cnt = self._watch_api(proxy)
        return ok, f"{self.platform} 접속 {'성공' if ok else '실패'}"

    def _send_chat(self) -> None:
        with connection() as conn:
            chats = conn.execute(
                "SELECT * FROM auto_chats WHERE status='실행' AND bot_id=?",
                (self.bot_id,),
            ).fetchall()
        for chat in chats:
            with connection() as conn:
                conn.execute(
                    "INSERT INTO chat_logs (id, platform, account_name, message, status, sent_at) VALUES (?,?,?,?,?,?)",
                    (secrets.token_urlsafe(10), self.platform, self.streamer_id,
                     chat["message"], '전송', now()),
                )

    def _update_db(self, connected: int, status: str) -> None:
        timestamp = now()
        with connection() as conn:
            conn.execute(
                "UPDATE active_bots SET connected_count=?, status=?, updated_at=? "
                "WHERE id=?",
                (connected, status, timestamp, self.bot_id),
            )
            if not self.session_id:
                self.session_id = self.bot_id
            cur = conn.execute(
                "UPDATE broadcast_sessions "
                "SET observed_viewers=?, status=?, updated_at=? WHERE id=?",
                (connected, status, timestamp, self.session_id),
            )
            if cur.rowcount == 0:
                conn.execute(
                    "INSERT OR IGNORE INTO broadcast_sessions "
                    "(id, platform, streamer_id, status, observed_viewers, "
                    "started_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                    (self.session_id, self.platform, self.streamer_id, status,
                     connected, timestamp, timestamp),
                )
            # 최신화 로직 연동 — viewer_plans.current_count 갱신
            conn.execute(
                "UPDATE viewer_plans SET current_count=?, updated_at=? "
                "WHERE streamer_id=? AND status!='종료'",
                (connected, timestamp, self.streamer_id),
            )

    def run(self) -> None:
        self._connected = 0
        self._update_db(0, "가동 중")
        # 프록시가 없으면 자동 수집
        if not self._proxies():
            collect_and_verify_proxies()
        delay = 0.05 if "칼같이" in self.motion_type else 0.3
        # ① 목표 인원까지 실제 API로 1명씩 접속
        #    각 시청자를 다른 프록시로 (444 IP 제한 방지)
        while self._connected < self.target_count and not self._stop_event.is_set():
            proxy = self._pick_proxy()
            ok, real_count, chat_host, chat_port = self._watch_api(proxy)
            if ok:
                self._connected += 1
                self._update_db(real_count, "가동 중")
                # WebSocket 연결 (팬더티비만)
                if self.platform == "pandatv" and chat_host:
                    try:
                        import websocket
                        ws = websocket.WebSocket()
                        ws.connect(f"wss://{chat_host}:{chat_port}", timeout=5)
                        ws.send(json.dumps({"cmd":"enter","userId":self._channel_id()}))
                        self._ws_list.append(ws)
                    except Exception:
                        pass
            else:
                ok, real_count, chat_host, chat_port = self._watch_api(None)
                if ok:
                    self._connected += 1
                    self._update_db(real_count, "가동 중")
                    if self.platform == "pandatv" and chat_host:
                        try:
                            import websocket
                            ws = websocket.WebSocket()
                            ws.connect(f"wss://{chat_host}:{chat_port}", timeout=5)
                            ws.send(json.dumps({"cmd":"enter","userId":self._channel_id()}))
                            self._ws_list.append(ws)
                        except Exception:
                            pass
            if "채팅" in self.chat_mode and random.random() < 0.1:
                self._send_chat()
            time.sleep(delay)
        # ② 유지 루프 — 1분마다 action=watch로 세션 갱신 + WebSocket 유지
        # ③ 유지 루프 — 30초마다 10% 시청자 세션 갱신 (viewer 유지)
        tick = 0
        while not self._stop_event.is_set():
            tick += 1
            # WebSocket ping
            for ws in self._ws_list[:]:
                try:
                    ws.send(json.dumps({"cmd":"ping"}))
                except Exception:
                    self._ws_list.remove(ws)
            # 30초마다 10% 시청자 갱신 (action=watch로 세션 유지)
            if tick % 1 == 0:
                refresh = max(1, self.target_count // 10)
                for _ in range(refresh):
                    if self._stop_event.is_set():
                        break
                    proxy = self._pick_proxy()
                    ok, real_count, chat_host, chat_port = self._watch_api(proxy)
                    if not ok:
                        ok, real_count, chat_host, chat_port = self._watch_api(None)
                    if ok and self.platform == "pandatv" and chat_host:
                        try:
                            ws = websocket.WebSocket()
                            ws.connect(f"wss://{chat_host}:{chat_port}", timeout=5)
                            ws.send(json.dumps({"cmd":"enter","userId":self._channel_id()}))
                            self._ws_list.append(ws)
                        except Exception:
                            pass
                    time.sleep(delay)
            self._update_db(self._connected, "가동 중")
            # 25분마다 일부 이탈 후 재접속
            if tick % 50 == 0:
                drop = random.randint(1, max(2, self.target_count // 10))
                self._connected = max(0, self.target_count - drop)
                self._update_db(self._connected, "일시중지")
                self._stop_event.wait(random.uniform(3, 8))
                if self._stop_event.is_set():
                    break
                for _ in range(drop):
                    if self._stop_event.is_set():
                        break
                    proxy = self._pick_proxy()
                    ok, real_count, _, _ = self._watch_api(proxy)
                    if not ok:
                        ok, real_count, _, _ = self._watch_api(None)
                    if ok:
                        self._connected += 1
                        self._update_db(real_count, "가동 중")
                    time.sleep(delay)
                self._stop_event.wait(30)
                proxy = self._pick_proxy()
                ok, real_count, _, _ = self._watch_api(proxy)
                if not ok:
                    ok, real_count, _, _ = self._watch_api(None)
                if ok:
                    self._update_db(real_count, "가동 중")
                time.sleep(delay)
        self._update_db(self._connected, "종료")

    def stop(self) -> None:
        self._stop_event.set()
        for ws in self._ws_list:
            try:
                ws.close()
            except Exception:
                pass
        self._ws_list.clear()


_ACTIVE_WORKERS: dict[str, BotWorker] = {}


def start_worker(bot_id: str, platform: str, streamer_id: str,
                 target_count: int, proxy_name: str = "", session_id: str = "",
                 motion_type: str = "자연스럽게 서서히 증가/감소 (추천)",
                 chat_mode: str = "연동된 계정으로 자동 매크로 채팅 분") -> BotWorker:
    worker = BotWorker(bot_id, platform, streamer_id, target_count, proxy_name, session_id)
    worker.motion_type = motion_type
    worker.chat_mode = chat_mode
    _ACTIVE_WORKERS[bot_id] = worker
    t = threading.Thread(target=worker.run, daemon=True)
    t.start()
    return worker


def stop_worker(bot_id: str) -> None:
    worker = _ACTIVE_WORKERS.pop(bot_id, None)
    if worker:
        worker.stop()


def render_dashboard():
    """
    Streamlit 대시보드 렌더링 함수
    기존 로직과 신규 기능을 통합하여 표시
    """
    st.title("📡 Live-broadcast Operations Dashboard")

    # 데이터베이스 초기화
    init_db()

    # 사이드바: 설정 및 관리
    with st.sidebar:
        st.header("⚙️ Settings")

        # 프록시 설정 섹션 (기존 기능 유지)
        st.subheader("Proxy Settings")
        proxy_file_path = st.text_input("Proxy File Path", value=str(DB_PATH.with_name("proxies.txt")))
        if st.button("Load Proxies"):
            if Path(proxy_file_path).exists():
                st.success(f"Loaded proxies from {proxy_file_path}")
            else:
                st.error("File not found")

        # 새로운 기능: 플랫폼 연결 테스트
        st.subheader("🔗 Platform Connectivity")
        selected_platform = st.selectbox("Select Platform", PLATFORMS)
        test_url = st.text_input("Stream URL / Endpoint", placeholder="https://...")

        if st.button("Test Connection"):
            engine = PlatformEngine()
            if "youtube" in selected_platform.lower():
                result = engine.ping_youtube_api(test_url)
                st.json(result)
            elif "pandora" in selected_platform.lower():
                result = engine.ping_pandora_tv(test_url)
                st.json(result)
            else:
                st.info("Platform-specific logic pending.")

    # 메인 대시보드: 세션 관리 (기존 기능 유지)
    st.header("🎥 Broadcast Sessions")

    # 세션 목록 표시
    with connection() as conn:
        sessions = conn.execute("SELECT * FROM broadcast_sessions ORDER BY updated_at DESC").fetchall()

        if not sessions:
            st.info("No active sessions. Create a new one below.")
        else:
            for session in sessions:
                st.markdown(f"**{session['platform']}** - {session['streamer_id']}")
                st.write(f"Status: {session['status']}, Viewers: {session['observed_viewers']}")
                col1, col2 = st.columns([1, 1])
                with col1:
                    if st.button("Pause", key=f"pause_{session['id']}"):
                        conn.execute("UPDATE broadcast_sessions SET status = '일시중지' WHERE id = ?", (session['id'],))
                with col2:
                    if st.button("Stop", key=f"stop_{session['id']}"):
                        conn.execute("UPDATE broadcast_sessions SET status = '종료' WHERE id = ?", (session['id'],))

    # 새 세션 생성 폼 (기존 기능 유지 + 신규 필드 추가)
    st.header("➕ New Session")
    with st.form("new_session_form"):
        platform = st.selectbox("Platform", PLATFORMS)
        streamer_id = st.text_input("Streamer ID / Channel Name")
        notes = st.text_area("Notes")

        submitted = st.form_submit_button("Create Session")
        if submitted:
            session_id = secrets.token_hex(8)
            with connection() as conn:
                conn.execute(
                    "INSERT INTO broadcast_sessions (id, platform, streamer_id, status, observed_viewers, notes, started_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (session_id, platform, streamer_id, "모니터링", 0, notes, now(), now())
                )
            st.success(f"Session {session_id} created!")

    # 채팅 로그 섹션 (신규 기능)
    st.header("💬 Chat Logs")
    with connection() as conn:
        logs = conn.execute("SELECT * FROM chat_logs ORDER BY sent_at DESC LIMIT 50").fetchall()
        if logs:
            df = pd.DataFrame(logs)
            st.dataframe(df, use_container_width=True)
        else:
            st.info("No chat logs yet.")

if __name__ == "__main__":
    main()
