from __future__ import annotations

import re
import socket
import sqlite3
from pathlib import Path

import pytest

try:
    from streamlit.testing.v1 import AppTest
except (ImportError, ModuleNotFoundError) as exc:
    raise RuntimeError(
        "프로젝트 테스트는 requirements-dev.txt가 설치된 가상환경에서 실행하세요 "
        "(예: .\\venv\\Scripts\\python.exe -m pytest)."
    ) from exc

import ytpdbot_app as app

BROADCAST_URL = "https://live.example.com/channel/streamer-01"


def test_public_proxy_source_catalog_is_flattened_without_missing_feeds():
    expected_names = {
        "TheSpeedX / SOCKS-List",
        "Proxifly / free-proxy-list",
        "Proxy-Free / free-proxy-list",
        "ProxyScrape / free-proxy-list",
        "Proxmint / free-proxy-list",
        "Dahono / free-proxy-list",
        "Litport / free-proxy-list",
        "Maximilian Feix / free-proxy-list",
        "HProxy / free-proxy-list",
        "IPLocate / free-proxy-list",
        "Thordata / awesome-free-proxy-list",
        "Proxio / proxy-list",
        "Databay Labs / free-proxy-list",
        "FusionProxies / free-proxy-list",
        "Fyvri / fresh-proxy-list",
    }

    assert {source["name"] for source in app.PUBLIC_PROXY_SOURCE_CATALOG} == expected_names
    assert len(app.PUBLIC_PROXY_SOURCES) == 45
    assert len(set(app.PUBLIC_PROXY_SOURCES)) == len(app.PUBLIC_PROXY_SOURCES)
    assert {scheme for scheme, _ in app.PUBLIC_PROXY_SOURCES} == {
        "http",
        "socks4",
        "socks5",
    }
    assert app.PUBLIC_PROXY_SOURCES == [
        feed
        for source in app.PUBLIC_PROXY_SOURCE_CATALOG
        for feed in source["feeds"]
    ]


@pytest.fixture
def isolated_database(tmp_path, monkeypatch):
    database = tmp_path / "test.sqlite3"
    monkeypatch.setattr(app, "DB_PATH", database)
    app.init_db()
    return database


def test_database_enables_wal_and_creates_required_tables(isolated_database):
    with sqlite3.connect(isolated_database) as connection:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        proxy_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(proxy_settings)")
        }
    assert journal_mode == "wal"
    assert {
        "whitelist",
        "broadcast_sessions",
        "proxy_settings",
        "accounts",
        "active_bots",
        "auto_chats",
        "viewer_plans",
    } <= tables
    assert {
        "source",
        "last_checked_at",
        "health_status",
        "quarantined",
        "failure_reason",
        "last_exit_ip",
        "latency_ms",
    } <= proxy_columns


def test_remote_database_requires_auth_token(monkeypatch):
    monkeypatch.setenv("TURSO_DATABASE_URL", "https://powerbot.example.turso.io")
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="TURSO_AUTH_TOKEN"):
        with app.connection():
            pass


def test_remote_database_uses_turso_connection(monkeypatch):
    import turso_serverless

    events = []

    class FakeConnection:
        row_factory = None

        def execute(self, sql, parameters=()):
            events.append(("execute", sql, parameters))
            return self

        def commit(self):
            events.append(("commit",))

        def rollback(self):
            events.append(("rollback",))

        def close(self):
            events.append(("close",))

    fake_connection = FakeConnection()

    def fake_connect(url, *, auth_token):
        events.append(("connect", url, auth_token))
        return fake_connection

    monkeypatch.setenv("TURSO_DATABASE_URL", "https://powerbot.example.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-token")
    monkeypatch.setattr(turso_serverless, "connect", fake_connect)

    with app.connection() as connection:
        assert connection is fake_connection

    assert fake_connection.row_factory is turso_serverless.Row
    assert events == [
        ("connect", "https://powerbot.example.turso.io", "test-token"),
        ("execute", "PRAGMA foreign_keys = ON", ()),
        ("commit",),
        ("close",),
    ]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(" streamer-01 ", "streamer-01"), ("한글   계정", "한글 계정")],
)
def test_normalized_identifier(raw, expected):
    assert app.normalized_identifier(raw, "식별자") == expected


def test_normalized_identifier_rejects_empty_and_too_long():
    with pytest.raises(ValueError):
        app.normalized_identifier("   ", "식별자")
    with pytest.raises(ValueError):
        app.normalized_identifier("x" * 101, "식별자")


@pytest.mark.parametrize("raw", ["line\nbreak", "tab\tvalue", "delete\x7fvalue"])
def test_normalized_identifier_rejects_control_characters(raw):
    with pytest.raises(ValueError, match="제어 문자"):
        app.normalized_identifier(raw, "식별자")


def test_proxy_endpoint_normalization():
    assert app.normalize_proxy_endpoint("HTTP://Example.COM:8080") == "http://example.com:8080"
    with pytest.raises(ValueError):
        app.normalize_proxy_endpoint("http://user:password@example.com:8080")


def test_proxy_inventory_validates_formats_and_deduplicates():
    endpoints, errors, duplicates = app.parse_proxy_inventory(
        "\n".join(
            [
                "HTTP://Example.COM:8080",
                "http://example.com:8080",
                "https://secure.example:443",
                "socks4://legacy.example:1080",
                "socks5://modern.example:1080",
                "ftp://invalid.example:21",
                "http://user:secret@example.com:8080",
            ]
        )
    )

    assert endpoints == [
        "http://example.com:8080",
        "https://secure.example:443",
        "socks4://legacy.example:1080",
        "socks5://modern.example:1080",
    ]
    assert duplicates == 1
    assert len(errors) == 2
    assert all("secret" not in error for error in errors)


def test_proxy_inventory_import_tracks_source_and_existing_duplicates(
    isolated_database,
):
    first = app.import_proxy_inventory(
        "http://one.example:8080\nsocks5://two.example:1080\ninvalid",
        "공개 테스트 목록",
    )
    second = app.import_proxy_inventory(
        "HTTP://ONE.EXAMPLE:8080\nhttps://three.example:443",
        "업데이트 목록",
    )

    assert first == {
        "imported": 2,
        "duplicates": 0,
        "invalid": 1,
        "errors": ["3행: 프록시 형식은 http, https, socks4, socks5만 지원합니다."],
    }
    assert second["imported"] == 1
    assert second["duplicates"] == 1
    with sqlite3.connect(isolated_database) as connection:
        rows = connection.execute(
            "SELECT endpoint, source, health_status, quarantined "
            "FROM proxy_settings ORDER BY endpoint"
        ).fetchall()
    assert rows == [
        ("http://one.example:8080", "공개 테스트 목록", "미점검", 0),
        ("https://three.example:443", "업데이트 목록", "미점검", 0),
        ("socks5://two.example:1080", "공개 테스트 목록", "미점검", 0),
    ]


def test_public_proxy_collection_fetches_checks_and_saves_live_endpoints(
    isolated_database, monkeypatch
):
    payloads = {
        "https://lists.example/http.txt": (
            b"one.example:8080\ninvalid\nuser:secret@hidden.example:80\n"
        ),
        "https://lists.example/socks5.txt": (
            b"socks5://two.example:1080\nhttp://one.example:8080\n"
        ),
    }

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self, size=-1):
            return self.payload if size < 0 else self.payload[:size]

    def fake_urlopen(request, timeout):
        del timeout
        return FakeResponse(payloads[request.full_url])

    def fake_check(endpoint, timeout_seconds):
        del timeout_seconds
        if endpoint == "socks5://two.example:1080":
            raise ConnectionRefusedError("offline")

    monkeypatch.setattr(
        app,
        "PUBLIC_PROXY_SOURCES",
        [
            ("http", "https://lists.example/http.txt"),
            ("socks5", "https://lists.example/socks5.txt"),
        ],
    )
    monkeypatch.setattr(app.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(app, "check_proxy_connection", fake_check)

    result = app.collect_and_verify_proxies(max_collect=10, workers=4)

    assert result == {
        "collected": 2,
        "alive": 1,
        "saved": 1,
        "removed": 0,
        "reconciled": 1,
        "source_failures": 0,
    }
    with sqlite3.connect(isolated_database) as connection:
        row = connection.execute(
            "SELECT endpoint, source, enabled, health_status, quarantined "
            "FROM proxy_settings"
        ).fetchone()
    assert row == ("http://one.example:8080", "자동 수집", 0, "정상", 0)


def test_public_proxy_collection_accepts_bounded_prefix_of_large_feed(
    isolated_database, monkeypatch
):
    first_row = b"large-feed.example:8080\n"
    payload = first_row + (b"# padding\n" * (app.MAX_PROXY_IMPORT_BYTES // 4))

    class LargeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self, size=-1):
            return payload if size < 0 else payload[:size]

    monkeypatch.setattr(
        app,
        "PUBLIC_PROXY_SOURCES",
        [("http", "https://lists.example/large.txt")],
    )
    monkeypatch.setattr(
        app.urllib.request,
        "urlopen",
        lambda request, timeout: LargeResponse(),
    )
    monkeypatch.setattr(
        app,
        "check_proxy_connection",
        lambda endpoint, timeout_seconds: None,
    )

    result = app.collect_and_verify_proxies(max_collect=10, workers=2)

    assert result["collected"] == 1
    assert result["alive"] == 1
    assert result["saved"] == 1
    assert result["source_failures"] == 0


def test_public_proxy_refresh_replaces_stale_auto_pool_and_reassigns_work(
    isolated_database, monkeypatch
):
    timestamp = app.now()
    app.save_proxy(
        "이전 자동",
        "http://stale.example:8080",
        source="자동 수집",
    )
    app.save_proxy(
        "수동 유지",
        "http://manual.example:8080",
        source="직접 등록",
    )
    with app.connection() as connection:
        connection.execute(
            "INSERT INTO active_bots VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "bot",
                "플랫폼",
                "streamer",
                "account",
                "이전 자동",
                "실행",
                timestamp,
                timestamp,
            ),
        )

    class ListResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self, size=-1):
            payload = b"fresh.example:8080\n"
            return payload if size < 0 else payload[:size]

    monkeypatch.setattr(
        app,
        "PUBLIC_PROXY_SOURCES",
        [("http", "https://lists.example/fresh.txt")],
    )
    monkeypatch.setattr(
        app.urllib.request,
        "urlopen",
        lambda request, timeout: ListResponse(),
    )
    monkeypatch.setattr(
        app,
        "check_proxy_connection",
        lambda endpoint, timeout_seconds: None,
    )

    result = app.collect_and_verify_proxies(max_collect=10, workers=2)
    assert result["removed"] == 1
    assert result["reconciled"] == 1
    with app.connection() as connection:
        assert connection.execute(
            "SELECT 1 FROM proxy_settings WHERE name='이전 자동'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM proxy_settings WHERE name='수동 유지'"
        ).fetchone() is not None
        fresh = connection.execute(
            "SELECT name FROM proxy_settings WHERE endpoint='http://fresh.example:8080'"
        ).fetchone()[0]

    class RouteResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ip": "203.0.113.31"}

    monkeypatch.setattr(
        app.requests,
        "get",
        lambda *args, **kwargs: RouteResponse(),
    )
    failover = app.activate_proxy_with_failover(
        fresh, timeout_seconds=1, max_candidates=1, workers=1
    )
    assert failover["reassigned_bots"] == 1
    with app.connection() as connection:
        bot = connection.execute(
            "SELECT proxy_name, status FROM active_bots WHERE id='bot'"
        ).fetchone()
    assert tuple(bot) == (fresh, "실행")


def test_collected_proxy_pool_enables_multiple_routes_and_reassigns_work(
    isolated_database, monkeypatch
):
    timestamp = app.now()
    for name, endpoint in (
        ("자동-1", "http://one.example:8080"),
        ("자동-2", "http://two.example:8080"),
        ("자동-실패", "http://bad.example:8080"),
    ):
        app.save_proxy(name, endpoint, enabled=False, source="자동 수집")
    with app.connection() as connection:
        connection.execute(
            "INSERT INTO active_bots VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "bot",
                "플랫폼",
                "streamer",
                "account",
                "자동-실패",
                "실행",
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            "INSERT INTO auto_chats VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("chat", "bot", "message", 30, "실행", timestamp, timestamp),
        )

    def fake_probe(endpoint, timeout_seconds):
        del timeout_seconds
        if "bad.example" in endpoint:
            raise app.requests.exceptions.ProxyError("offline")
        last_octet = "11" if "one.example" in endpoint else "12"
        latency = 20 if "one.example" in endpoint else 30
        return {"exit_ip": f"203.0.113.{last_octet}", "latency_ms": latency}

    monkeypatch.setattr(app, "_probe_proxy_route", fake_probe)
    result = app.activate_collected_proxy_pool(
        pool_size=2, timeout_seconds=1, max_candidates=3, workers=3
    )

    assert [winner["name"] for winner in result["winners"]] == ["자동-1", "자동-2"]
    assert result["discarded"] == ["자동-실패"]
    assert result["reassigned_bots"] == 1
    with app.connection() as connection:
        enabled = connection.execute(
            "SELECT name, last_exit_ip FROM proxy_settings WHERE enabled=1 ORDER BY name"
        ).fetchall()
        bot = connection.execute(
            "SELECT proxy_name, status FROM active_bots WHERE id='bot'"
        ).fetchone()
        chat = connection.execute(
            "SELECT status FROM auto_chats WHERE id='chat'"
        ).fetchone()[0]
        failed = connection.execute(
            "SELECT 1 FROM proxy_settings WHERE name='자동-실패'"
        ).fetchone()
    assert [tuple(row) for row in enabled] == [
        ("자동-1", "203.0.113.11"),
        ("자동-2", "203.0.113.12"),
    ]
    assert tuple(bot) == ("자동-1", "실행")
    assert chat == "실행"
    assert failed is None


def test_proxy_health_failure_discards_proxy(isolated_database, monkeypatch):
    assert app.save_proxy("점검 대상", "http://127.0.0.1:8080", source="직접 등록")

    def fail_connection(endpoint, timeout_seconds=3.0):
        raise ConnectionRefusedError("private diagnostic must not be stored")

    monkeypatch.setattr(app, "check_proxy_connection", fail_connection)
    assert app.check_proxy_health("점검 대상") is False
    with sqlite3.connect(isolated_database) as connection:
        failed = connection.execute(
            "SELECT 1 FROM proxy_settings WHERE name='점검 대상'"
        ).fetchone()
    assert failed is None


def test_verify_proxy_route_sends_request_through_proxy_and_activates(
    isolated_database, monkeypatch
):
    app.save_proxy(
        "실제 경유",
        "http://proxy.example:8080",
        enabled=False,
        source="테스트",
    )
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ip": "203.0.113.7"}

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse()

    elapsed = iter([10.0, 10.125])
    monkeypatch.setattr(app.requests, "get", fake_get)
    monkeypatch.setattr(app.time, "perf_counter", lambda: next(elapsed))

    result = app.verify_proxy_route("실제 경유")

    assert result == {
        "name": "실제 경유",
        "endpoint": "http://proxy.example:8080",
        "exit_ip": "203.0.113.7",
        "latency_ms": 125,
    }
    assert calls[0][0] == app.PROXY_EGRESS_CHECK_URL
    assert calls[0][1]["proxies"] == {
        "http": "http://proxy.example:8080",
        "https": "http://proxy.example:8080",
    }
    with sqlite3.connect(isolated_database) as connection:
        stored = connection.execute(
            "SELECT enabled, health_status, quarantined, last_exit_ip, latency_ms "
            "FROM proxy_settings WHERE name='실제 경유'"
        ).fetchone()
    assert stored == (1, "정상", 0, "203.0.113.7", 125)


def test_verify_proxy_route_failure_discards_proxy(
    isolated_database, monkeypatch
):
    app.save_proxy("실패 경유", "socks5://proxy.example:1080", source="테스트")

    def fail_get(*args, **kwargs):
        del args, kwargs
        raise app.requests.exceptions.ProxyError("private failure detail")

    monkeypatch.setattr(app.requests, "get", fail_get)

    with pytest.raises(ValueError, match="폐기"):
        app.verify_proxy_route("실패 경유")

    with sqlite3.connect(isolated_database) as connection:
        stored = connection.execute(
            "SELECT 1 FROM proxy_settings WHERE name='실패 경유'"
        ).fetchone()
    assert stored is None


def test_proxy_failover_discards_failure_and_reassigns_running_work(
    isolated_database, monkeypatch
):
    timestamp = app.now()
    app.save_proxy("실패 후보", "http://bad.example:8080", source="테스트")
    app.save_proxy("정상 후보", "http://good.example:8080", source="테스트")
    with app.connection() as connection:
        connection.execute(
            "INSERT INTO active_bots VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "bot",
                "플랫폼",
                "streamer",
                "account",
                "실패 후보",
                "실행",
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            "INSERT INTO auto_chats VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("chat", "bot", "message", 30, "실행", timestamp, timestamp),
        )

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ip": "203.0.113.21"}

    def fake_get(url, **kwargs):
        del url
        if kwargs["proxies"]["https"] == "http://bad.example:8080":
            raise app.requests.exceptions.ProxyError("unreachable")
        return FakeResponse()

    monkeypatch.setattr(app.requests, "get", fake_get)
    result = app.activate_proxy_with_failover(
        "실패 후보", timeout_seconds=1, max_candidates=2, workers=2
    )

    assert result["name"] == "정상 후보"
    assert result["discarded"] == ["실패 후보"]
    assert result["reassigned_bots"] == 1
    with app.connection() as connection:
        assert connection.execute(
            "SELECT 1 FROM proxy_settings WHERE name='실패 후보'"
        ).fetchone() is None
        winner = connection.execute(
            "SELECT enabled, health_status, last_exit_ip "
            "FROM proxy_settings WHERE name='정상 후보'"
        ).fetchone()
        bot = connection.execute(
            "SELECT proxy_name, status FROM active_bots WHERE id='bot'"
        ).fetchone()
        chat_status = connection.execute(
            "SELECT status FROM auto_chats WHERE id='chat'"
        ).fetchone()[0]
    assert tuple(winner) == (1, "정상", "203.0.113.21")
    assert tuple(bot) == ("정상 후보", "실행")
    assert chat_status == "실행"


def test_proxy_connection_check_uses_endpoint_tcp_reachability():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        app.check_proxy_connection(f"socks5://127.0.0.1:{port}", 0.5)
    finally:
        listener.close()

    with pytest.raises(OSError):
        app.check_proxy_connection(f"http://127.0.0.1:{port}", 0.2)


def test_failed_proxy_pauses_dependent_bot_and_chat(isolated_database, monkeypatch):
    timestamp = app.now()
    with app.connection() as connection:
        connection.execute(
            "INSERT INTO proxy_settings (name, endpoint, enabled, updated_at) "
            "VALUES (?, ?, 1, ?)",
            ("proxy", "http://127.0.0.1:8080", timestamp),
        )
        connection.execute(
            "INSERT INTO active_bots VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("bot", "플랫폼", "streamer", "account", "proxy", "실행", timestamp, timestamp),
        )
        connection.execute(
            "INSERT INTO auto_chats VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("chat", "bot", "message", 30, "실행", timestamp, timestamp),
        )

    def fail_connection(endpoint, timeout_seconds=3.0):
        raise OSError("unreachable")

    monkeypatch.setattr(app, "check_proxy_connection", fail_connection)
    assert app.check_proxy_health("proxy") is False
    with app.connection() as connection:
        proxy = connection.execute(
            "SELECT enabled, quarantined, health_status FROM proxy_settings "
            "WHERE name='proxy'"
        ).fetchone()
        bot_status = connection.execute(
            "SELECT status FROM active_bots WHERE id='bot'"
        ).fetchone()[0]
        chat_status = connection.execute(
            "SELECT status FROM auto_chats WHERE id='chat'"
        ).fetchone()[0]
    assert proxy is None
    assert bot_status == "일시중지"
    assert chat_status == "일시중지"


def test_inventory_health_check_fails_over_running_work(
    isolated_database, monkeypatch
):
    timestamp = app.now()
    app.save_proxy("실패 프록시", "http://bad.example:8080", source="테스트")
    app.save_proxy("정상 프록시", "http://good.example:8080", source="테스트")
    with app.connection() as connection:
        connection.execute(
            "INSERT INTO active_bots VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "bot",
                "팬더티비",
                BROADCAST_URL,
                "계정",
                "실패 프록시",
                "실행",
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            "INSERT INTO auto_chats VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("chat", "bot", "메시지", 30, "실행", timestamp, timestamp),
        )

    def fake_tcp_check(endpoint, timeout_seconds):
        del timeout_seconds
        if "bad.example" in endpoint:
            raise OSError("unreachable")

    monkeypatch.setattr(app, "check_proxy_connection", fake_tcp_check)
    monkeypatch.setattr(
        app,
        "_probe_proxy_route",
        lambda endpoint, timeout: {
            "endpoint": endpoint,
            "exit_ip": "203.0.113.20",
            "latency_ms": 12,
        },
    )

    assert app.check_proxy_inventory_health() == {
        "실패 프록시": False,
        "정상 프록시": True,
    }
    with sqlite3.connect(isolated_database) as connection:
        assert connection.execute(
            "SELECT 1 FROM proxy_settings WHERE name='실패 프록시'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT proxy_name, status FROM active_bots WHERE id='bot'"
        ).fetchone() == ("정상 프록시", "실행")
        assert connection.execute(
            "SELECT status FROM auto_chats WHERE id='chat'"
        ).fetchone() == ("실행",)


def test_disabling_account_pauses_dependent_bot_and_chat(isolated_database):
    timestamp = app.now()
    with app.connection() as connection:
        connection.execute(
            "INSERT INTO accounts VALUES (?, ?, ?, 1, ?)",
            ("account", "플랫폼", "계정", timestamp),
        )
        connection.execute(
            "INSERT INTO active_bots VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("bot", "플랫폼", "streamer", "계정", "", "실행", timestamp, timestamp),
        )
        connection.execute(
            "INSERT INTO auto_chats VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("chat", "bot", "message", 30, "실행", timestamp, timestamp),
        )
    app.set_account_enabled("account", False)
    with app.connection() as connection:
        account_enabled = connection.execute(
            "SELECT enabled FROM accounts WHERE id='account'"
        ).fetchone()[0]
        bot_status = connection.execute(
            "SELECT status FROM active_bots WHERE id='bot'"
        ).fetchone()[0]
        chat_status = connection.execute(
            "SELECT status FROM auto_chats WHERE id='chat'"
        ).fetchone()[0]
    assert account_enabled == 0
    assert bot_status == "일시중지"
    assert chat_status == "일시중지"


def test_save_account_reports_duplicate_without_creating_another_row(isolated_database):
    assert app.save_account("유튜브 라이브", "검증 계정") is True
    assert app.save_account("유튜브 라이브", "검증 계정") is False
    with app.connection() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM accounts WHERE platform=? AND account_name=?",
            ("유튜브 라이브", "검증 계정"),
        ).fetchone()[0]
    assert count == 1


def test_proxy_schema_migration_deduplicates_without_breaking_bot_reference(
    tmp_path, monkeypatch
):
    database = tmp_path / "old-proxy-schema.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE proxy_settings (name TEXT PRIMARY KEY, endpoint TEXT NOT NULL, "
            "enabled INTEGER NOT NULL, updated_at TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE active_bots (id TEXT PRIMARY KEY, platform TEXT NOT NULL, "
            "streamer_id TEXT NOT NULL, account_name TEXT NOT NULL, "
            "proxy_name TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, "
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO proxy_settings VALUES (?, ?, 1, ?)",
            [
                ("기존 A", "http://duplicate.example:8080", app.now()),
                ("기존 B", "http://duplicate.example:8080", app.now()),
            ],
        )
        connection.execute(
            "INSERT INTO active_bots VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("bot", "플랫폼", "방송", "계정", "기존 B", "종료", app.now(), app.now()),
        )

    monkeypatch.setattr(app, "DB_PATH", database)
    app.init_db()

    with sqlite3.connect(database) as connection:
        proxies = connection.execute(
            "SELECT name, endpoint FROM proxy_settings"
        ).fetchall()
        bot_proxy = connection.execute(
            "SELECT proxy_name FROM active_bots WHERE id='bot'"
        ).fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO proxy_settings (name, endpoint, enabled, updated_at) "
                "VALUES ('다시 중복', 'http://duplicate.example:8080', 1, ?)",
                (app.now(),),
            )
    assert proxies == [("기존 A", "http://duplicate.example:8080")]
    assert bot_proxy == "기존 A"


def test_broadcast_url_normalization_and_platform_detection():
    assert (
        app.normalize_broadcast_url(" HTTPS://YouTube.COM/live/abc#chat ")
        == "https://youtube.com/live/abc"
    )
    assert app.platform_from_broadcast_url("https://www.youtube.com/live/abc") == "유튜브 라이브"
    assert app.platform_from_broadcast_url(BROADCAST_URL) == "기타 방송"
    for invalid in ("streamer-01", "ftp://example.com/live", "https://user:pw@example.com/live"):
        with pytest.raises(ValueError):
            app.normalize_broadcast_url(invalid)


def test_stopping_bot_stops_its_chat(isolated_database):
    timestamp = app.now()
    with app.connection() as connection:
        connection.execute(
            "INSERT INTO active_bots VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("bot", "플랫폼", "streamer", "account", "", "실행", timestamp, timestamp),
        )
        connection.execute(
            "INSERT INTO auto_chats VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("chat", "bot", "message", 30, "실행", timestamp, timestamp),
        )
    app.set_bot_status("bot", "종료")
    with app.connection() as connection:
        status = connection.execute(
            "SELECT status FROM auto_chats WHERE id='chat'"
        ).fetchone()[0]
    assert status == "종료"


@pytest.mark.parametrize(("disabled_resource", "message"), [("account", "계정"), ("proxy", "프록시")])
def test_resuming_bot_rechecks_disabled_dependencies(
    isolated_database, disabled_resource, message
):
    timestamp = app.now()
    with app.connection() as connection:
        connection.execute(
            "INSERT INTO accounts VALUES (?, ?, ?, ?, ?)",
            ("account", "플랫폼", "계정", 1, timestamp),
        )
        connection.execute(
            "INSERT INTO proxy_settings "
            "(name, endpoint, enabled, updated_at, health_status, last_exit_ip) "
            "VALUES (?, ?, ?, ?, '정상', '203.0.113.8')",
            ("proxy", "http://127.0.0.1:8080", 1, timestamp),
        )
    bot_id = app.start_bot("플랫폼", BROADCAST_URL, "account", "proxy")
    app.set_bot_status(bot_id, "일시중지")
    table = "accounts" if disabled_resource == "account" else "proxy_settings"
    key = "id" if disabled_resource == "account" else "name"
    value = "account" if disabled_resource == "account" else "proxy"
    with app.connection() as connection:
        connection.execute(f"UPDATE {table} SET enabled=0 WHERE {key}=?", (value,))

    with pytest.raises(ValueError, match=message):
        app.set_bot_status(bot_id, "실행")
    with app.connection() as connection:
        status = connection.execute(
            "SELECT status FROM active_bots WHERE id=?", (bot_id,)
        ).fetchone()[0]
    assert status == "일시중지"


def test_start_bot_rejects_tcp_only_proxy_without_route_proof(isolated_database):
    timestamp = app.now()
    with app.connection() as connection:
        connection.execute(
            "INSERT INTO accounts VALUES (?, ?, ?, ?, ?)",
            ("account", "플랫폼", "계정", 1, timestamp),
        )
        connection.execute(
            "INSERT INTO proxy_settings "
            "(name, endpoint, enabled, updated_at, health_status, last_exit_ip) "
            "VALUES (?, ?, 1, ?, '정상', '')",
            ("tcp-only", "http://127.0.0.1:8080", timestamp),
        )
    with pytest.raises(ValueError, match="프록시"):
        app.start_bot("플랫폼", BROADCAST_URL, "account", "tcp-only")


def test_start_bot_rechecks_account_and_prevents_active_duplicate(isolated_database):
    timestamp = app.now()
    with app.connection() as connection:
        connection.execute(
            "INSERT INTO accounts VALUES (?, ?, ?, ?, ?)",
            ("account", "플랫폼", "계정", 1, timestamp),
        )
    bot_id = app.start_bot("플랫폼", BROADCAST_URL, "account")
    assert bot_id
    with pytest.raises(ValueError, match="이미 활성"):
        app.start_bot("플랫폼", BROADCAST_URL, "account")

    app.set_bot_status(bot_id, "종료")
    with app.connection() as connection:
        connection.execute("UPDATE accounts SET enabled=0 WHERE id='account'")
    with pytest.raises(ValueError, match="사용 가능한 계정"):
        app.start_bot("플랫폼", BROADCAST_URL, "account")


def test_start_auto_chat_rechecks_bot_status(isolated_database):
    timestamp = app.now()
    with app.connection() as connection:
        connection.execute(
            "INSERT INTO active_bots VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("bot", "플랫폼", "streamer", "account", "", "일시중지", timestamp, timestamp),
        )
    with pytest.raises(ValueError, match="실행 중인 봇"):
        app.start_auto_chat("bot", "message", 30)


@pytest.mark.parametrize(
    ("message", "interval", "error"),
    [
        ("   ", 30, "메시지"),
        ("x" * 501, 30, "500자"),
        ("message", 0, "1초"),
        ("message", True, "정수"),
    ],
)
def test_start_auto_chat_validates_boundary_values(
    isolated_database, message, interval, error
):
    with pytest.raises(ValueError, match=error):
        app.start_auto_chat("missing", message, interval)


def test_viewer_plans_accept_multiple_registered_quantities(isolated_database):
    with app.connection() as connection:
        connection.execute(
            "INSERT INTO whitelist VALUES (?, ?)", (BROADCAST_URL, app.now())
        )

    first = app.add_viewer_plan(
        "기타 방송", BROADCAST_URL, "기본 묶음 A", 100, "첫 계획"
    )
    second = app.add_viewer_plan(
        "기타 방송", BROADCAST_URL, "추가 묶음 B", 200
    )

    plans = app.records("viewer_plans")
    assert {row["id"] for row in plans} == {first, second}
    assert sum(row["viewer_count"] for row in plans) == 300
    assert {row["status"] for row in plans} == {"대기"}

    app.set_viewer_plan_status(first, "일시중지")
    with app.connection() as connection:
        status = connection.execute(
            "SELECT status FROM viewer_plans WHERE id=?", (first,)
        ).fetchone()[0]
    assert status == "일시중지"


def test_stopping_instant_viewer_plan_clears_planned_count(isolated_database):
    with app.connection() as connection:
        connection.execute(
            "INSERT INTO whitelist VALUES (?, ?)", (BROADCAST_URL, app.now())
        )
    plan_id = app.add_viewer_plan(
        "기타 방송",
        BROADCAST_URL,
        "즉시 투입 계획",
        100,
        motion_type=app.MOTION_TYPES[1],
    )
    with app.connection() as connection:
        assert connection.execute(
            "SELECT current_count FROM viewer_plans WHERE id=?", (plan_id,)
        ).fetchone()[0] == 100

    app.set_viewer_plan_status(plan_id, "종료")

    with app.connection() as connection:
        row = connection.execute(
            "SELECT status, current_count FROM viewer_plans WHERE id=?", (plan_id,)
        ).fetchone()
    assert tuple(row) == ("종료", 0)


@pytest.mark.parametrize("quantity", [0, -1, 10, 21, 1510, True])
def test_viewer_plan_rejects_invalid_quantity(isolated_database, quantity):
    with app.connection() as connection:
        connection.execute(
            "INSERT INTO whitelist VALUES (?, ?)", (BROADCAST_URL, app.now())
        )
    with pytest.raises(ValueError, match="20~1500"):
        app.add_viewer_plan(
            "기타 방송", BROADCAST_URL, "잘못된 계획", quantity
        )


def test_viewer_plan_requires_whitelisted_broadcast_url(isolated_database):
    with pytest.raises(ValueError, match="화이트리스트"):
        app.add_viewer_plan(
            "기타 방송", "https://live.example.com/not-allowed", "기본 묶음", 100
        )


def test_direct_url_registration_auto_allowlists_target(isolated_database):
    plan_id = app.register_viewer_plan_from_url(BROADCAST_URL, 100)
    assert plan_id
    assert app.whitelist() == [BROADCAST_URL]
    plan = app.records("viewer_plans")[0]
    assert plan["streamer_id"] == BROADCAST_URL
    assert plan["platform"] == "기타 방송"
    assert plan["duration_hours"] == 2
    assert plan["started_at"]
    assert plan["ends_at"]


def test_viewer_plan_timer_expires_locally(isolated_database):
    plan_id = app.register_viewer_plan_from_url(
        BROADCAST_URL, 100, duration_hours=1
    )
    with app.connection() as connection:
        connection.execute(
            "UPDATE viewer_plans SET ends_at='2000-01-01T00:00:00+00:00' WHERE id=?",
            (plan_id,),
        )
    assert app.expire_viewer_plans() == 1
    plan = app.records("viewer_plans")[0]
    assert plan["status"] == "종료"
    assert plan["current_count"] == 0


def test_gradual_viewer_plan_updates_local_progress(isolated_database):
    plan_id = app.register_viewer_plan_from_url(BROADCAST_URL, 100)
    timestamp = app.datetime.now(app.timezone.utc)
    with app.connection() as connection:
        connection.execute(
            "UPDATE viewer_plans SET started_at=?, ends_at=?, current_count=0 WHERE id=?",
            (
                (timestamp - app.timedelta(hours=1)).isoformat(timespec="seconds"),
                (timestamp + app.timedelta(hours=1)).isoformat(timespec="seconds"),
                plan_id,
            ),
        )
    assert app.refresh_viewer_plan_progress() == 1
    plan = app.records("viewer_plans")[0]
    assert 0 < plan["current_count"] <= 100


def test_viewer_plan_restores_original_1500_limit_and_owner_quota(
    isolated_database,
):
    with app.connection() as connection:
        connection.execute(
            "INSERT INTO whitelist VALUES (?, ?)", (BROADCAST_URL, app.now())
        )

    app.add_viewer_plan("기타 방송", BROADCAST_URL, "최솟값", 20)
    maximum = app.add_viewer_plan(
        "기타 방송", BROADCAST_URL, "최댓값", 1500
    )
    assert maximum
    with app.connection() as connection:
        connection.execute("UPDATE viewer_plans SET status='종료'")
    app.add_viewer_plan(
        "기타 방송", BROADCAST_URL, "사장님 첫 계획", 1500, operator="사장님(나)"
    )
    app.add_viewer_plan(
        "기타 방송", BROADCAST_URL, "사장님 추가 계획", 500, operator="사장님(나)"
    )
    with pytest.raises(ValueError, match="잔여 할당량"):
        app.add_viewer_plan(
            "기타 방송", BROADCAST_URL, "사장님 초과", 20, operator="사장님(나)"
        )

    assert app.MIN_VIEWER_COUNT == 20
    assert app.MAX_VIEWER_COUNT == 1500
    assert app.VIEWER_COUNT_STEP == 10
    assert list(app.PARTNER_LIMITS) == ["사장님(나)"]
    assert set(app.PARTNER_LIMITS.values()) == {2000}


def test_deploy_script_preserves_existing_secret_file():
    script = Path(__file__).with_name("deploy.sh").read_text(encoding="utf-8")
    guard = 'if [[ ! -e "${ENV_FILE}" ]]'
    install = 'install -m 600 -o root -g root /dev/null "${ENV_FILE}"'
    assert guard in script
    assert install in script
    assert script.index(guard) < script.index(install)
    assert "YTPDBOT_ADMIN_PASSWORD=............" in script
    assert script.index("grep -q '^YTPDBOT_ADMIN_PASSWORD=............'") < script.index(
        'useradd --system'
    )
    assert 'DOMAIN must be a valid hostname' in script
    assert "(?:" not in script
    assert 'REQUIRED_COMMANDS=(id useradd install grep nginx systemctl curl)' in script
    assert "Python 3.11 or newer is required." in script
    assert script.index("/_stcore/health") > script.index(
        'systemctl enable --now "${SERVICE_NAME}.service"'
    )
    assert script.index("systemctl reload nginx") > script.index(
        'if [[ "${service_ready}" -ne 1 ]]'
    )
    assert script.count("/_stcore/health") == 2


@pytest.mark.parametrize(
    ("domain", "valid"),
    [
        ("dashboard.example.com", True),
        ("a.io", True),
        ("-bad.example.com", False),
        ("bad-.example.com", False),
        ("example", False),
        ("example.com\nserver_name injected.example", False),
    ],
)
def test_deploy_domain_pattern_rejects_invalid_or_injected_values(domain, valid):
    script = Path(__file__).with_name("deploy.sh").read_text(encoding="utf-8")
    pattern = re.search(r"^DOMAIN_PATTERN='([^']+)'$", script, re.MULTILINE)
    assert pattern is not None
    assert (re.fullmatch(pattern.group(1), domain) is not None) is valid


def test_streamlit_login_and_dashboard_render(tmp_path, monkeypatch):
    database = tmp_path / "ui.sqlite3"
    monkeypatch.setenv("YTPDBOT_ADMIN_PASSWORD", "test-password")
    monkeypatch.setenv("YTPDBOT_DB_PATH", str(database))
    monkeypatch.setenv("RENDER_GIT_COMMIT", "0123456789abcdef")
    app_test = AppTest.from_file(str(Path(__file__).with_name("ytpdbot_app.py")))

    app_test.run(timeout=10)
    assert not app_test.exception
    assert app_test.title[0].value == "방송 운영 대시보드 로그인"
    assert any("배포 버전: 01234567" in item.value for item in app_test.caption)
    assert not database.exists()

    app_test.text_input[0].input("test-password")
    app_test.button[0].click().run(timeout=10)
    assert not app_test.exception
    assert app_test.title[0].value == "라이브 방송 통합 오케스트레이터"
    assert database.exists()
    assert any(
        button.label == "공개 프록시 전체 자동 수집·검증"
        for button in app_test.button
    )
    assert any(
        "자동 수집 출처 15곳 · 공개 목록 45개" in caption.value
        for caption in app_test.caption
    )
    rendered_source_list = " ".join(item.value for item in app_test.markdown)
    for source_name in (
        "TheSpeedX / SOCKS-List",
        "Proxifly / free-proxy-list",
        "Proxy-Free / free-proxy-list",
        "ProxyScrape / free-proxy-list",
        "Proxmint / free-proxy-list",
        "Dahono / free-proxy-list",
        "Litport / free-proxy-list",
        "Maximilian Feix / free-proxy-list",
        "HProxy / free-proxy-list",
        "IPLocate / free-proxy-list",
        "Thordata / awesome-free-proxy-list",
        "Proxio / proxy-list",
        "Databay Labs / free-proxy-list",
        "FusionProxies / free-proxy-list",
        "Fyvri / fresh-proxy-list",
    ):
        assert source_name in rendered_source_list
    rendered_captions = " ".join(caption.value for caption in app_test.caption)
    assert "외부 플랫폼 트래픽은 직접 생성하지 않습니다" not in rendered_captions
    assert "상태 점검은 TCP 도달성만" not in rendered_captions
    assert "인증정보가 포함된 주소는 저장하지 않습니다" not in rendered_captions

    next(
        button for button in app_test.button if button.label == "로그아웃"
    ).click().run(timeout=10)
    assert not app_test.exception
    assert app_test.title[0].value == "방송 운영 대시보드 로그인"


def test_streamlit_expires_stale_authenticated_session(tmp_path, monkeypatch):
    monkeypatch.setenv("YTPDBOT_ADMIN_PASSWORD", "test-password")
    monkeypatch.setenv("YTPDBOT_DB_PATH", str(tmp_path / "expired-session.sqlite3"))
    app_test = AppTest.from_file(str(Path(__file__).with_name("ytpdbot_app.py")))
    app_test.session_state["authenticated"] = True
    app_test.session_state["authenticated_at"] = 0.0

    app_test.run(timeout=10)

    assert not app_test.exception
    assert app_test.title[0].value == "방송 운영 대시보드 로그인"
    assert any("세션이 만료" in warning.value for warning in app_test.warning)


def test_streamlit_refreshes_active_session_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("YTPDBOT_ADMIN_PASSWORD", "test-password")
    monkeypatch.setenv(
        "YTPDBOT_DB_PATH", str(tmp_path / "active-session.sqlite3")
    )
    app_test = AppTest.from_file(str(Path(__file__).with_name("ytpdbot_app.py")))
    previous_activity = (
        app.datetime.now(app.timezone.utc).timestamp()
        - app.SESSION_TIMEOUT_SECONDS
        + 10
    )
    app_test.session_state["authenticated"] = True
    app_test.session_state["authenticated_at"] = previous_activity

    app_test.run(timeout=10)

    assert not app_test.exception
    assert app_test.title[0].value == "라이브 방송 통합 오케스트레이터"
    assert app_test.session_state["authenticated_at"] > previous_activity


def test_streamlit_locks_login_after_repeated_failures(tmp_path, monkeypatch):
    monkeypatch.setenv("YTPDBOT_ADMIN_PASSWORD", "test-password")
    monkeypatch.setenv("YTPDBOT_DB_PATH", str(tmp_path / "login-lock.sqlite3"))
    app_test = AppTest.from_file(str(Path(__file__).with_name("ytpdbot_app.py")))
    app_test.run(timeout=10)

    for _ in range(app.MAX_LOGIN_ATTEMPTS):
        app_test.text_input[0].input("wrong-password")
        app_test.button[0].click().run(timeout=10)

    assert not app_test.exception
    assert any("로그인을 잠급니다" in error.value for error in app_test.error)


def test_streamlit_local_desktop_mode_starts_without_password(tmp_path, monkeypatch):
    database = tmp_path / "local-desktop.sqlite3"
    monkeypatch.delenv("YTPDBOT_ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("YTPDBOT_LOCAL_DESKTOP", "1")
    monkeypatch.setenv("YTPDBOT_DB_PATH", str(database))
    app_test = AppTest.from_file(str(Path(__file__).with_name("ytpdbot_app.py")))

    app_test.run(timeout=10)

    assert not app_test.exception
    assert app_test.title[0].value == "라이브 방송 통합 오케스트레이터"
    assert "관리자 비밀번호" not in [field.label for field in app_test.text_input]
    assert "목표 투입 인원수 설정" in [field.label for field in app_test.slider]
    assert "방송 URL" in [field.label for field in app_test.text_input]
    assert "방송 플랫폼 선택" in [field.label for field in app_test.selectbox]
    assert not any(
        "스트리머 ID" in field.label or "BJ ID" in field.label
        for field in app_test.text_input
    )
    assert "가동 계획 시작" in [button.label for button in app_test.button]
    assert "자동 종료 타이머" in [field.label for field in app_test.selectbox]
    assert "프록시 일괄 붙여넣기" in [field.label for field in app_test.text_area]
    assert "프록시 파일 가져오기" in [field.label for field in app_test.file_uploader]
    assert "일괄 가져오기" in [button.label for button in app_test.button]
    assert "시청자 묶음 이름" not in [item.label for item in app_test.text_input]
    assert [tab.label for tab in app_test.tabs] == [
        "봇 & 채팅 실시간 제어 센터",
        "방송 화이트리스트 관리",
        "플랫폼/P2P 연동 계정 설정",
        "자동 매크로 챗 문구 셋업",
    ]

    control_url = next(
        field for field in app_test.text_input if field.key == "control_broadcast_url"
    )
    control_url.input(BROADCAST_URL)
    next(
        button for button in app_test.button if button.label == "가동 계획 시작"
    ).click().run(
        timeout=10
    )
    assert not app_test.exception
    with sqlite3.connect(database) as connection:
        plan = connection.execute(
            "SELECT platform, streamer_id, viewer_count FROM viewer_plans"
        ).fetchone()
        allowed = connection.execute(
            "SELECT streamer_id FROM whitelist"
        ).fetchone()
    assert plan == ("팬더티비", BROADCAST_URL, 100)
    assert allowed == (BROADCAST_URL,)


def test_streamlit_viewer_plan_lifecycle_buttons(tmp_path, monkeypatch):
    database = tmp_path / "viewer-plan-buttons.sqlite3"
    monkeypatch.setenv("YTPDBOT_LOCAL_DESKTOP", "1")
    monkeypatch.setenv("YTPDBOT_DB_PATH", str(database))
    app_test = AppTest.from_file(str(Path(__file__).with_name("ytpdbot_app.py")))

    app_test.run(timeout=10)
    control_url = next(
        field for field in app_test.text_input if field.key == "control_broadcast_url"
    )
    control_url.input(BROADCAST_URL)
    next(
        button for button in app_test.button if button.label == "가동 계획 시작"
    ).click().run(timeout=10)

    assert not app_test.exception
    with sqlite3.connect(database) as connection:
        plan_id, status = connection.execute(
            "SELECT id, status FROM viewer_plans"
        ).fetchone()
    assert status == "대기"

    next(
        button
        for button in app_test.button
        if button.key == f"pause_viewer_plan_{plan_id}"
    ).click().run(timeout=10)
    with sqlite3.connect(database) as connection:
        status = connection.execute(
            "SELECT status FROM viewer_plans WHERE id=?", (plan_id,)
        ).fetchone()[0]
    assert status == "일시중지"

    next(
        button
        for button in app_test.button
        if button.key == f"resume_viewer_plan_{plan_id}"
    ).click().run(timeout=10)
    with sqlite3.connect(database) as connection:
        status = connection.execute(
            "SELECT status FROM viewer_plans WHERE id=?", (plan_id,)
        ).fetchone()[0]
    assert status == "대기"

    next(
        button
        for button in app_test.button
        if button.key == f"stop_viewer_plan_{plan_id}"
    ).click().run(timeout=10)
    with sqlite3.connect(database) as connection:
        status, current_count = connection.execute(
            "SELECT status, current_count FROM viewer_plans WHERE id=?", (plan_id,)
        ).fetchone()
    assert status == "종료"
    assert current_count == 0

    next(
        checkbox
        for checkbox in app_test.checkbox
        if checkbox.label == "종료된 계획 표시"
    ).set_value(True).run(timeout=10)
    assert any(
        button.key == f"stop_viewer_plan_{plan_id}"
        for button in app_test.button
    ) is False
    assert any(BROADCAST_URL in info.value for info in app_test.info) is False


def test_streamlit_broadcast_management_buttons(tmp_path, monkeypatch):
    database = tmp_path / "broadcast-management-buttons.sqlite3"
    monkeypatch.setenv("YTPDBOT_LOCAL_DESKTOP", "1")
    monkeypatch.setenv("YTPDBOT_DB_PATH", str(database))
    app_test = AppTest.from_file(str(Path(__file__).with_name("ytpdbot_app.py")))

    app_test.run(timeout=10)
    next(
        field for field in app_test.text_input if field.key == "control_broadcast_url"
    ).input(BROADCAST_URL)
    next(
        button for button in app_test.button if button.label == "가동 계획 시작"
    ).click().run(timeout=10)

    next(
        field for field in app_test.text_input if field.key == "monitor_broadcast_url"
    ).input(BROADCAST_URL)
    next(
        field for field in app_test.number_input if field.label == "실제 관측 시청자 수"
    ).set_value(37)
    next(
        button for button in app_test.button if button.label == "등록"
    ).click().run(timeout=10)

    assert not app_test.exception
    with sqlite3.connect(database) as connection:
        session_id, status, observed = connection.execute(
            "SELECT id, status, observed_viewers FROM broadcast_sessions"
        ).fetchone()
    assert (status, observed) == ("모니터링", 37)

    next(
        field for field in app_test.selectbox if field.label == "상태"
    ).select("종료")
    next(
        field for field in app_test.number_input if field.label == "관측 시청자 수"
    ).set_value(41)
    next(
        button for button in app_test.button if button.label == "저장"
    ).click().run(timeout=10)

    with sqlite3.connect(database) as connection:
        status, observed = connection.execute(
            "SELECT status, observed_viewers FROM broadcast_sessions WHERE id=?",
            (session_id,),
        ).fetchone()
    assert (status, observed) == ("종료", 41)

    extra_url = "https://live.example.com/channel/allowlist-button"
    next(
        field for field in app_test.text_input if field.key == "allowlist_url"
    ).input(extra_url)
    next(
        button for button in app_test.button if button.label == "추가"
    ).click().run(timeout=10)
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM whitelist WHERE streamer_id=?", (extra_url,)
        ).fetchone()[0] == 1


def test_streamlit_connection_bot_and_chat_buttons(tmp_path, monkeypatch):
    database = tmp_path / "connection-bot-chat-buttons.sqlite3"
    monkeypatch.setenv("YTPDBOT_LOCAL_DESKTOP", "1")
    monkeypatch.setenv("YTPDBOT_DB_PATH", str(database))

    class FakeProxyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ip": "203.0.113.19"}

    monkeypatch.setattr(app.requests, "get", lambda *args, **kwargs: FakeProxyResponse())
    app_test = AppTest.from_file(str(Path(__file__).with_name("ytpdbot_app.py")))

    app_test.run(timeout=10)
    next(
        field for field in app_test.text_input if field.label == "프록시 이름"
    ).input("검증 프록시")
    next(
        field for field in app_test.text_input if field.label == "프록시 주소"
    ).input("http://127.0.0.1:8080")
    next(
        button for button in app_test.button if button.label == "프록시 저장"
    ).click().run(timeout=10)

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT endpoint, enabled FROM proxy_settings WHERE name='검증 프록시'"
        ).fetchone() == ("http://127.0.0.1:8080", 1)
    assert any(
        button.key == "route_proxy_검증 프록시" for button in app_test.button
    )

    proxy_toggle = next(
        button
        for button in app_test.button
        if button.key == "toggle_proxy_검증 프록시"
    )
    proxy_toggle.click().run(timeout=10)
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT enabled FROM proxy_settings WHERE name='검증 프록시'"
        ).fetchone()[0] == 0
    next(
        button
        for button in app_test.button
        if button.key == "toggle_proxy_검증 프록시"
    ).click().run(timeout=10)
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT enabled, last_exit_ip FROM proxy_settings "
            "WHERE name='검증 프록시'"
        ).fetchone() == (1, "203.0.113.19")

    next(
        field for field in app_test.text_input if field.label == "계정 이름"
    ).input("검증 계정")
    next(
        button for button in app_test.button if button.label == "계정 추가"
    ).click().run(timeout=10)
    with sqlite3.connect(database) as connection:
        account_id, enabled = connection.execute(
            "SELECT id, enabled FROM accounts WHERE account_name='검증 계정'"
        ).fetchone()
    assert enabled == 1

    next(
        button
        for button in app_test.button
        if button.key == f"toggle_account_{account_id}"
    ).click().run(timeout=10)
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT enabled FROM accounts WHERE id=?", (account_id,)
        ).fetchone()[0] == 0
    next(
        button
        for button in app_test.button
        if button.key == f"toggle_account_{account_id}"
    ).click().run(timeout=10)

    next(
        field for field in app_test.text_input if field.label == "대상 방송 URL"
    ).input(BROADCAST_URL)
    proxy_select = next(
        field for field in app_test.selectbox if field.label == "사용 프록시"
    )
    proxy_option = next(
        option for option in proxy_select.options if option.startswith("검증 프록시 ·")
    )
    proxy_select.select(proxy_option)
    next(
        button for button in app_test.button if button.label == "봇 작업 등록"
    ).click().run(timeout=10)
    with sqlite3.connect(database) as connection:
        bot_id, status, proxy_name = connection.execute(
            "SELECT id, status, proxy_name FROM active_bots"
        ).fetchone()
    assert status == "실행"
    assert proxy_name == "검증 프록시"

    next(
        field for field in app_test.text_area if field.label == "채팅 메시지"
    ).input("버튼 회귀 검증 메시지")
    next(
        button for button in app_test.button if button.label == "채팅 계획 등록"
    ).click().run(timeout=10)
    with sqlite3.connect(database) as connection:
        chat_id, status = connection.execute(
            "SELECT id, status FROM auto_chats"
        ).fetchone()
    assert status == "실행"

    for action, expected in (("pause_chat", "일시중지"), ("resume_chat", "실행")):
        next(
            button
            for button in app_test.button
            if button.key == f"{action}_{chat_id}"
        ).click().run(timeout=10)
        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT status FROM auto_chats WHERE id=?", (chat_id,)
            ).fetchone()[0] == expected

    next(
        button
        for button in app_test.button
        if button.key == f"stop_chat_{chat_id}"
    ).click().run(timeout=10)
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT status FROM auto_chats WHERE id=?", (chat_id,)
        ).fetchone()[0] == "종료"

    for action, expected in (("pause_bot", "일시중지"), ("resume_bot", "실행")):
        next(
            button
            for button in app_test.button
            if button.key == f"{action}_{bot_id}"
        ).click().run(timeout=10)
        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT status FROM active_bots WHERE id=?", (bot_id,)
            ).fetchone()[0] == expected

    next(
        button
        for button in app_test.button
        if button.key == f"stop_bot_{bot_id}"
    ).click().run(timeout=10)
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT status FROM active_bots WHERE id=?", (bot_id,)
        ).fetchone()[0] == "종료"


def test_windows_one_click_launcher_is_loopback_only_and_uses_e_drive_data():
    root = Path(__file__).parent
    script = (root / "start_local.ps1").read_text(encoding="utf-8-sig")
    launcher = (root / "파워봇 실행.cmd").read_text(encoding="utf-8-sig")

    assert "YTPDBOT_LOCAL_DESKTOP" in script
    assert "YTPDBOT_DB_PATH" in script
    assert "E:\\YN_AUTO\\data\\powerbot" in script
    assert "--server.address=127.0.0.1" in script
    assert "--server.port=8501" in script
    assert "YTPDBOT_ADMIN_PASSWORD=" not in script
    assert "start_local.ps1" in launcher


def test_streamlit_rejects_short_admin_password(tmp_path, monkeypatch):
    monkeypatch.setenv("YTPDBOT_ADMIN_PASSWORD", "short")
    monkeypatch.setenv("YTPDBOT_DB_PATH", str(tmp_path / "short-password.sqlite3"))
    app_test = AppTest.from_file(str(Path(__file__).with_name("ytpdbot_app.py")))

    app_test.run(timeout=10)
    assert not app_test.exception
    assert "6자 이상" in app_test.error[0].value


def test_streamlit_accepts_six_character_admin_password(tmp_path, monkeypatch):
    monkeypatch.setenv("YTPDBOT_ADMIN_PASSWORD", "abc123")
    monkeypatch.setenv("YTPDBOT_DB_PATH", str(tmp_path / "six-character-password.sqlite3"))
    app_test = AppTest.from_file(str(Path(__file__).with_name("ytpdbot_app.py")))

    app_test.run(timeout=10)
    app_test.text_input[0].input("abc123")
    app_test.button[0].click().run(timeout=10)

    assert not app_test.exception
    assert app_test.title[0].value == "라이브 방송 통합 오케스트레이터"


def test_streamlit_starts_bot_from_dashboard(tmp_path, monkeypatch):
    database = tmp_path / "start-bot.sqlite3"
    monkeypatch.setenv("YTPDBOT_ADMIN_PASSWORD", "test-password")
    monkeypatch.setenv("YTPDBOT_DB_PATH", str(database))
    monkeypatch.setattr(app, "DB_PATH", database)
    app.init_db()
    with app.connection() as connection:
        connection.execute(
            "INSERT INTO accounts VALUES (?, ?, ?, ?, ?)",
            ("account", "팬더티비", "운영 계정", 1, app.now()),
        )

    app_test = AppTest.from_file(str(Path(__file__).with_name("ytpdbot_app.py")))
    app_test.session_state["authenticated"] = True
    app_test.run(timeout=10)
    assert not app_test.exception

    streamer_input = next(
        widget
        for widget in app_test.text_input
        if widget.label == "대상 방송 URL"
    )
    start_button = next(
        widget for widget in app_test.button if widget.label == "봇 작업 등록"
    )
    streamer_input.input(BROADCAST_URL)
    start_button.click().run(timeout=10)

    assert not app_test.exception
    with sqlite3.connect(database) as connection:
        bot = connection.execute(
            "SELECT platform, streamer_id, account_name, status FROM active_bots"
        ).fetchone()
    assert bot == ("팬더티비", BROADCAST_URL, "운영 계정", "실행")


def test_streamlit_reports_disabled_account_when_resuming_bot(tmp_path, monkeypatch):
    database = tmp_path / "resume-bot.sqlite3"
    monkeypatch.setenv("YTPDBOT_ADMIN_PASSWORD", "test-password")
    monkeypatch.setenv("YTPDBOT_DB_PATH", str(database))
    monkeypatch.setattr(app, "DB_PATH", database)
    app.init_db()
    timestamp = app.now()
    with app.connection() as connection:
        connection.execute(
            "INSERT INTO accounts VALUES (?, ?, ?, ?, ?)",
            ("account", "팬더티비", "운영 계정", 0, timestamp),
        )
        connection.execute(
            "INSERT INTO active_bots VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "bot",
                "팬더티비",
                "streamer-01",
                "운영 계정",
                "",
                "일시중지",
                timestamp,
                timestamp,
            ),
        )

    app_test = AppTest.from_file(str(Path(__file__).with_name("ytpdbot_app.py")))
    app_test.session_state["authenticated"] = True
    app_test.run(timeout=10)
    assert not app_test.exception

    resume_button = next(
        widget for widget in app_test.button if widget.label == "다시 시작"
    )
    resume_button.click().run(timeout=10)

    assert not app_test.exception
    assert any("사용 가능한 계정" in error.value for error in app_test.error)
    with sqlite3.connect(database) as connection:
        status = connection.execute(
            "SELECT status FROM active_bots WHERE id='bot'"
        ).fetchone()[0]
    assert status == "일시중지"
