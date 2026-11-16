from __future__ import annotations

import re
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
            "INSERT INTO proxy_settings VALUES (?, ?, ?, ?)",
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


def test_viewer_plan_restores_original_1500_limit_and_equal_partner_quota(
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
    app.add_viewer_plan(
        "기타 방송", BROADCAST_URL, "Sewon 첫 계획", 1500, operator="Sewon"
    )
    app.add_viewer_plan(
        "기타 방송", BROADCAST_URL, "Sewon 추가 계획", 500, operator="Sewon"
    )
    with pytest.raises(ValueError, match="잔여 할당량"):
        app.add_viewer_plan(
            "기타 방송", BROADCAST_URL, "Sewon 초과", 20, operator="Sewon"
        )

    assert app.MIN_VIEWER_COUNT == 20
    assert app.MAX_VIEWER_COUNT == 1500
    assert app.VIEWER_COUNT_STEP == 10
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
    monkeypatch.setenv("YTPDBOT_ADMIN_PASSWORD", "test-password")
    monkeypatch.setenv("YTPDBOT_DB_PATH", str(tmp_path / "ui.sqlite3"))
    app_test = AppTest.from_file(str(Path(__file__).with_name("ytpdbot_app.py")))

    app_test.run(timeout=10)
    assert not app_test.exception
    assert app_test.title[0].value == "방송 운영 대시보드 로그인"

    app_test.text_input[0].input("test-password")
    app_test.button[0].click().run(timeout=10)
    assert not app_test.exception
    assert app_test.title[0].value == "라이브 방송 운영 대시보드"


def test_streamlit_local_desktop_mode_starts_without_password(tmp_path, monkeypatch):
    database = tmp_path / "local-desktop.sqlite3"
    monkeypatch.delenv("YTPDBOT_ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("YTPDBOT_LOCAL_DESKTOP", "1")
    monkeypatch.setenv("YTPDBOT_DB_PATH", str(database))
    app_test = AppTest.from_file(str(Path(__file__).with_name("ytpdbot_app.py")))

    app_test.run(timeout=10)

    assert not app_test.exception
    assert app_test.title[0].value == "라이브 방송 운영 대시보드"
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
    assert "시청자 묶음 이름" not in [item.label for item in app_test.text_input]
    assert [tab.label for tab in app_test.tabs] == [
        "중앙 제어",
        "방송 관리",
        "연동 설정",
        "채팅 설정",
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
    assert "12자 이상" in app_test.error[0].value


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
