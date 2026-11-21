# 파워봇 배포 문서

Streamlit 1.67.0 + pandas 3.1.1 + requests 2.34.2 + turso_serverless 0.1.0 (2026-11-15 PyPI 최신 고정).

## 핵심 기능

- **무료 프록시로 플랫폼 접속**: proxy_settings 테이블에 등록된 무료 프록시로 팬더티비·플렉스티비·유튜브 라이브에 실제 HTTP 접속
- **자연스러운 빠졌다-접속했다**: BotWorker 백그라운드 스레드가 2~5초마다 시청자 수를 ±10~15% 변동시키고, 8% 확률로 프록시에서 떨어져 0명이 된 뒤 3~8초 대기 후 재접속
- **최신화 로직 연동**: refresh_viewer_plan_progress()가 viewer_plans.current_count를 시간 경과에 따라 갱신하고, BotWorker가 broadcast_sessions / active_bots / viewer_plans 세 테이블을 동시에 업데이트해서 대시보드에 실시간 시청자 수 표시

## Windows 원클릭 실행

`파워봇 실행.cmd` 더블클릭 → venv Streamlit 127.0.0.1:8501 → 기본 브라우저로 대시보드 열림. 로컬 전용, 외부 포트 미공개. DB: `bot_state.db`. PID: `streamlit.pid`.

`파워봇 종료.cmd` 더블클릭 → PID + 명령행 확인 후 Streamlit 프로세스 종료.

## 로컬 검증

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

Windows PowerShell: `.venv\Scripts\python.exe`. `conftest.py`는 임시 파일을 `test-temp`에 격리.

## 무료 공개 배포: Render + Turso (현재 사용 중)

`render.yaml` Blueprint로 Render 무료 Web Service 생성. 세 비밀값 입력:

```text
YTPDBOT_ADMIN_PASSWORD
TURSO_DATABASE_URL
TURSO_AUTH_TOKEN
```

Render 무료 서비스는 15분 무접속 시 중지, 다음 접속 시 재시작. 로컬 디스크는 영구 저장소가 아니므로 운영 데이터는 Turso에 저장.

**배포 완료 판정 기준**: `/_stcore/health` 응답 OK, 관리자 로그인 성공, Turso 쓰기 + 새로고침 후 재조회 성공.

## Streamlit Community Cloud + Turso (대체 경로)

Streamlit Community Cloud에 `ytpdbot_app.py` 진입점으로 배포. Secrets에 세 값 등록:

```toml
YTPDBOT_ADMIN_PASSWORD=[REDACTED]
TURSO_DATABASE_URL = "https://발급받은-DB-주소"
TURSO_AUTH_TOKEN=[REDACTED]
```

## Ubuntu + Nginx 배포

1. `ytpdbot_app.py`, `requirements.txt`, `deploy.sh`를 `/opt/ytpdbot`에 복사.
2. `/etc/ytpdbot.env` (root:600)에 `YTPDBOT_ADMIN_PASSWORD=12자-이상` 설정.
3. `sudo APP_DIR=/opt/ytpdbot DOMAIN=dashboard.example.com ./deploy.sh` 실행.
4. Nginx/로드 밸런서에 TLS 인증서 설정 → HTTPS 확인.
5. 브라우저에서 로그인·화이트리스트·세션 상태 변경·로그아웃 확인.

`deploy.sh`는 기존 env 파일 덮어쓰지 않음. Python 3.11 이상 확인 → 서비스 시작 → `/_stcore/health` HTTP 확인.