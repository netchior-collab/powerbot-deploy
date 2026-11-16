"""Live-broadcast operations dashboard.

This file is the runnable control surface for the dashboard in this folder.
The proxy, account, bot and chat control records reconstruct the recoverable
control surface while preserving the existing monitoring dashboard.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import socket
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
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
MAX_PROXY_IMPORT_LINES = 5000
MAX_PROXY_IMPORT_BYTES = 1024 * 1024
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
                failure_reason TEXT NOT NULL DEFAULT ''
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
        proxy_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(proxy_settings)")
        }
        proxy_migrations = {
            "source": "TEXT NOT NULL DEFAULT '직접 등록'",
            "last_checked_at": "TEXT NOT NULL DEFAULT ''",
            "health_status": "TEXT NOT NULL DEFAULT '미점검'",
            "quarantined": "INTEGER NOT NULL DEFAULT 0",
            "failure_reason": "TEXT NOT NULL DEFAULT ''",
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
            "quarantined=0, failure_reason=''",
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
                "VALUES (?, ?, 1, ?, ?, '', '미점검', 0, '')",
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
                conn.execute(
                    "UPDATE proxy_settings SET enabled=0, health_status='실패', "
                    "quarantined=1, failure_reason=?, last_checked_at=?, updated_at=? "
                    "WHERE name=?",
                    (reason, checked_at, checked_at, name),
                )
                affected_bots = conn.execute(
                    "SELECT id FROM active_bots WHERE proxy_name=? AND status='실행'",
                    (name,),
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
                    "WHERE name=? AND enabled=1 AND quarantined=0",
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
                "WHERE name=? AND enabled=1 AND quarantined=0",
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
    return bot_id


def start_auto_chat(bot_id: str, message: str, interval_seconds: int) -> str:
    """Create chat work only while its bot is still running."""
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


def main() -> None:
    st.set_page_config(page_title="라이브 방송 통합 오케스트레이터", layout="wide")
    login()
    database_identity = os.getenv("TURSO_DATABASE_URL", "").strip() or str(
        DB_PATH.resolve()
    )
    init_db_once(database_identity)
    expire_viewer_plans()
    refresh_viewer_plan_progress()
    title_column, session_column = st.columns([5, 1])
    title_column.title("라이브 방송 통합 오케스트레이터")
    if session_column.button("로그아웃", use_container_width=True):
        st.session_state.authenticated = False
        st.session_state.pop("authenticated_at", None)
        st.rerun()
    st.caption(
        "중앙 제어, 화이트리스트, 플랫폼 계정·프록시, 채팅 문구를 원본 흐름대로 관리합니다. "
        "현재 버전은 실행 계획과 상태를 기록하며 외부 플랫폼 트래픽은 직접 생성하지 않습니다."
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
                register_viewer_plan_from_url(
                    viewer_streamer,
                    int(viewer_count),
                    platform=viewer_platform,
                    operator=viewer_operator,
                    motion_type=viewer_motion,
                    chat_mode=viewer_chat_mode,
                    duration_hours=int(viewer_duration),
                )
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.success(
                    "로컬 가동 계획을 저장했습니다. 외부 트래픽은 생성하지 않았습니다."
                )
                st.rerun()

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
                        st.rerun()
        elif viewer_plan_rows:
            st.info("활성 계획이 없습니다. 종료된 계획 표시는 위 선택란에서 켤 수 있습니다.")
        else:
            st.info("등록된 시청자 계획이 없습니다.")

    with proxy_tab:
        st.subheader("플랫폼/P2P 프록시 인벤토리")
        st.caption(
            "http/https/socks4/socks5 엔드포인트를 관리합니다. 상태 점검은 TCP 도달성만 "
            "확인하며 시청자·시청시간·채팅 트래픽을 만들지 않습니다. 인증정보가 포함된 "
            "주소는 저장하지 않습니다."
        )
        with st.form("proxy_form"):
            proxy_name = st.text_input("프록시 이름").strip()
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
                    enabled=proxy_enabled,
                    source=proxy_source,
                )
            except ValueError as exc:
                st.error(str(exc))
            else:
                if saved:
                    st.success("프록시 설정을 저장했습니다.")
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
                st.success(
                    f"가져오기 완료: 신규 {result['imported']}개, "
                    f"중복 제외 {result['duplicates']}개, 형식 오류 {result['invalid']}개"
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
                    f"실패·격리 {len(proxies) - passed}개"
                )
                st.rerun()
            display_rows = [
                {
                    "이름": proxy["name"],
                    "주소": proxy["endpoint"],
                    "활성": bool(proxy["enabled"]),
                    "상태": proxy["health_status"],
                    "격리": bool(proxy["quarantined"]),
                    "출처": proxy["source"],
                    "마지막 점검": proxy["last_checked_at"] or "미점검",
                }
                for proxy in proxies
            ]
            st.dataframe(
                pd.DataFrame(display_rows),
                width="stretch",
                hide_index=True,
            )
            for proxy in proxies:
                label = (
                    f"{proxy['name']} · {proxy['health_status']} · "
                    f"{'격리' if proxy['quarantined'] else '사용 가능'}"
                )
                with st.expander(label):
                    st.write(f"출처: {proxy['source']}")
                    st.write(f"마지막 점검: {proxy['last_checked_at'] or '미점검'}")
                    c1, c2 = st.columns(2)
                    if c1.button(
                        "연결 상태 점검", key=f"check_proxy_{proxy['name']}"
                    ):
                        check_proxy_health(proxy["name"])
                        st.rerun()
                    next_enabled = not bool(proxy["enabled"])
                    action = "사용 중지" if proxy["enabled"] else "다시 사용"
                    if c2.button(action, key=f"toggle_proxy_{proxy['name']}"):
                        try:
                            set_proxy_enabled(proxy["name"], next_enabled)
                        except ValueError as exc:
                            st.error(str(exc))
                        else:
                            st.rerun()
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
            row for row in proxies if row["enabled"] and not row["quarantined"]
        ]
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


if __name__ == "__main__":
    main()
