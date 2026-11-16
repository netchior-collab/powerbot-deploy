#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/ytpdbot}"
APP_USER="${APP_USER:-ytpdbot}"
DOMAIN="${DOMAIN:-_}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SERVICE_NAME="ytpdbot"
ENV_FILE="/etc/ytpdbot.env"
DOMAIN_PATTERN='^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$'
REQUIRED_COMMANDS=(id useradd install grep nginx systemctl curl)

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo APP_DIR=/opt/ytpdbot DOMAIN=example.com ./deploy.sh" >&2
  exit 1
fi

if [[ ! -f "${APP_DIR}/ytpdbot_app.py" || ! -f "${APP_DIR}/requirements.txt" ]]; then
  echo "Copy ytpdbot_app.py and requirements.txt to ${APP_DIR} first." >&2
  exit 1
fi

for required_command in "${REQUIRED_COMMANDS[@]}"; do
  if ! command -v "${required_command}" >/dev/null 2>&1; then
    echo "Required command is not installed: ${required_command}" >&2
    exit 1
  fi
done
if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "Python executable was not found: ${PYTHON_BIN}" >&2
  exit 1
fi
if ! "${PYTHON_BIN}" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
  echo "Python 3.11 or newer is required." >&2
  exit 1
fi

if [[ "${DOMAIN}" != "_" && ! "${DOMAIN}" =~ ${DOMAIN_PATTERN} ]]; then
  echo "DOMAIN must be a valid hostname such as dashboard.example.com." >&2
  exit 1
fi

if [[ ! -e "${ENV_FILE}" ]]; then
  install -m 600 -o root -g root /dev/null "${ENV_FILE}"
else
  chown root:root "${ENV_FILE}"
  chmod 600 "${ENV_FILE}"
fi
if ! grep -q '^YTPDBOT_ADMIN_PASSWORD=............' "${ENV_FILE}"; then
  echo "Set YTPDBOT_ADMIN_PASSWORD to at least 12 characters in ${ENV_FILE}, then rerun this script." >&2
  exit 1
fi

id -u "${APP_USER}" >/dev/null 2>&1 || useradd --system --home "${APP_DIR}" --shell /usr/sbin/nologin "${APP_USER}"
install -d -o "${APP_USER}" -g "${APP_USER}" "${APP_DIR}/data"

"${PYTHON_BIN}" -m venv "${APP_DIR}/.venv"
"${APP_DIR}/.venv/bin/python" -m pip install --upgrade pip
"${APP_DIR}/.venv/bin/python" -m pip install --requirement "${APP_DIR}/requirements.txt"

cat >/etc/systemd/system/${SERVICE_NAME}.service <<EOF
[Unit]
Description=YT Powerbot operations dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
Environment=YTPDBOT_DB_PATH=${APP_DIR}/data/broadcast_ops.db
ExecStart=${APP_DIR}/.venv/bin/streamlit run ${APP_DIR}/ytpdbot_app.py --server.address=127.0.0.1 --server.port=8501 --server.headless=true
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=${APP_DIR}/data

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/nginx/sites-available/${SERVICE_NAME} <<EOF
map \$http_upgrade \$connection_upgrade {
  default upgrade;
  '' close;
}

server {
  listen 80;
  server_name ${DOMAIN};
  client_max_body_size 20m;

  add_header X-Content-Type-Options nosniff always;
  add_header X-Frame-Options DENY always;
  add_header Referrer-Policy no-referrer always;

  location / {
    proxy_pass http://127.0.0.1:8501;
    proxy_http_version 1.1;
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_set_header Upgrade \$http_upgrade;
    proxy_set_header Connection \$connection_upgrade;
    proxy_read_timeout 86400;
  }
}
EOF

ln -sfn /etc/nginx/sites-available/${SERVICE_NAME} /etc/nginx/sites-enabled/${SERVICE_NAME}
nginx -t
systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}.service"

service_ready=0
for _ in {1..30}; do
  if curl --fail --silent --show-error http://127.0.0.1:8501/_stcore/health >/dev/null; then
    service_ready=1
    break
  fi
  sleep 1
done
if [[ "${service_ready}" -ne 1 ]]; then
  echo "The Streamlit service did not become healthy within 30 seconds." >&2
  systemctl --no-pager --full status "${SERVICE_NAME}.service" >&2 || true
  exit 1
fi

systemctl reload nginx
curl --fail --silent --show-error http://127.0.0.1/_stcore/health >/dev/null
systemctl --no-pager --full status "${SERVICE_NAME}.service"
