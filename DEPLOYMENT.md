# 배포 준비

이 앱은 Streamlit 1.67.0과 pandas 3.1.1을 사용하며, 2026-11-15 기준 PyPI 최신 버전으로 고정되어 있다.

이 배포 준비본은 로컬 SQLite 기반 방송 운영 기록 대시보드다. 외부 플랫폼 로그인,
시청 트래픽 생성, 봇 실행, 자동 채팅 전송 모듈은 포함하지 않는다. 화면의 봇 작업과
채팅 계획은 실행 예정 상태를 기록할 뿐 외부 요청을 보내지 않는다.

`시청자 계획` 탭에서는 원본 설계대로 목표 투입 인원을 20~1,500명 범위에서 10명
단위로 설정하고, 여러 시청자 묶음의 관리자·플랫폼·스트리머·모션·채팅 모드·상태·메모를 등록할 수 있다.
모든 작업 관리자의 누적 계획 한도는 차등 없이 동일하게 2,000명이다.
이 기능도 로컬 운영 계획을 기록하며 외부 플랫폼에 시청 트래픽을 발생시키지는 않는다.

## Windows 원클릭 실행

`파워봇 실행.cmd`를 더블클릭하면 기존 `venv`의 Streamlit을 `127.0.0.1:8501`에만
실행하고 기본 브라우저로 대시보드를 연다. 로컬 전용 모드에서는 외부에 포트를 공개하지
않으므로 관리자 로그인 화면을 생략한다. 운영 데이터는
`E:\YN_AUTO\data\powerbot\broadcast_ops.db`에 저장하고 PID는
`E:\YN_AUTO\state\powerbot_local\streamlit.pid`에 기록한다.

`파워봇 종료.cmd`를 더블클릭하면 PID와 실제 명령행이 이 앱과 일치하는지 확인한 뒤에만
로컬 Streamlit 프로세스를 종료한다. 이 실행은 PC 로컬 구동이며 인터넷 공개 배포가 아니다.

## 로컬 검증

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

Windows PowerShell에서는 `.venv\Scripts\python.exe`를 사용한다. 가상환경 폴더를
`streamlit`으로 이름 짓으면 패키지 이름과 충돌하므로 `.venv`를 유지한다.
저장소의 `conftest.py`는 관리형 Windows에서 시스템 임시 폴더 접근이 차단된 경우에도
각 테스트 실행의 임시 파일을 `test-temp` 아래 고유 경로에 격리한다.

## 무료 공개 배포: Streamlit Community Cloud + Turso

Streamlit Community Cloud에는 `ytpdbot_app.py`를 진입점으로 배포한다. Community
Cloud의 로컬 파일은 영구 저장소가 아니므로 운영 DB는 Turso 무료 DB를 사용한다.
Streamlit 앱 설정의 Secrets에 다음 세 값을 등록한다. 실제 값은 저장소에 커밋하지 않는다.

```toml
YTPDBOT_ADMIN_PASSWORD = "12자-이상-관리자-비밀번호"
TURSO_DATABASE_URL = "https://발급받은-DB-주소"
TURSO_AUTH_TOKEN = "발급받은-DB-토큰"
```

배포 후 로그인, 화이트리스트/시청자 계획 등록, 페이지 새로고침, 앱 재부팅 후 데이터
재조회까지 성공해야 원격 배포 완료로 판정한다. `TURSO_DATABASE_URL`을 설정하지 않은
로컬 실행은 기존 SQLite 파일을 그대로 사용한다.

## 무료 공개 배포 대체 경로: Render + Turso

GitHub OAuth 연결이 차단되는 경우 저장소 루트의 `render.yaml`로 Render 무료 Web
Service를 생성할 수 있다. Blueprint 생성 화면에서 아래 세 비밀값을 입력하며 저장소에는
커밋하지 않는다.

```text
YTPDBOT_ADMIN_PASSWORD
TURSO_DATABASE_URL
TURSO_AUTH_TOKEN
```

Render 무료 서비스는 15분 동안 요청이 없으면 중지되며 다음 접속 때 다시 시작된다.
로컬 디스크는 영구 저장소가 아니므로 운영 데이터는 계속 Turso에 저장한다. 배포 완료는
`/_stcore/health` 응답, 관리자 로그인, Turso 쓰기와 새로고침 후 재조회가 모두 확인된
경우에만 판정한다.

## Ubuntu + Nginx 배포

1. `ytpdbot_app.py`, `requirements.txt`, `deploy.sh`를 `/opt/ytpdbot`에 복사한다.
2. `/etc/ytpdbot.env`를 root 소유, 권한 `600`으로 만들고 다음 값을 설정한다.

   ```text
   YTPDBOT_ADMIN_PASSWORD=12자-이상-관리자-비밀번호
   ```

3. 실제 도메인을 지정해 배포한다.

   ```bash
   sudo APP_DIR=/opt/ytpdbot DOMAIN=dashboard.example.com ./deploy.sh
   ```

4. Nginx 또는 로드 밸런서에서 TLS 인증서를 설정하고 HTTPS 접속을 확인한다. Streamlit 공식 문서는 운영 환경에서 앱 자체가 아니라 역방향 프록시나 로드 밸런서의 TLS 종료를 권장한다.
5. 로그인, 화이트리스트 등록, 세션 상태 변경, 로그아웃을 실제 브라우저에서 확인한다.

`deploy.sh`는 기존 `/etc/ytpdbot.env`를 덮어쓰지 않는다. 배포 전에 필수 명령과
Python 3.11 이상을 확인하고, 서비스 시작 뒤 Streamlit 내부 상태와 Nginx 프록시 상태를
각각 HTTP로 확인한 경우에만 성공한다. 실제 외부 플랫폼 실행 모듈은 이 복원본에 포함되어
있지 않으므로, 현재 배포 범위는 운영 상태를 기록·제어하는 웹 대시보드다.
