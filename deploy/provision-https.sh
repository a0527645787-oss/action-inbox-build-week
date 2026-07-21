#!/usr/bin/env bash
set -euo pipefail

readonly APP_DIR="/opt/actioninbox/repo"
readonly ENV_FILE="/opt/actioninbox/.env"
readonly PUBLIC_HOSTNAME="actioninbox.16-192-83-71.nip.io"
readonly EXPECTED_IP="16.192.83.71"
readonly COMPOSE_FILE="deploy/docker-compose.production.yml"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script with sudo." >&2
  exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Protected deployment environment file is missing." >&2
  exit 1
fi

resolved_ips="$(getent ahostsv4 "${PUBLIC_HOSTNAME}" | awk '{print $1}' | sort -u)"
if ! grep -Fxq "${EXPECTED_IP}" <<<"${resolved_ips}"; then
  echo "Public hostname does not resolve to the expected Elastic IP." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y certbot

cd "${APP_DIR}"
compose=(docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}")

"${compose[@]}" stop nginx
restore_http_proxy() {
  "${compose[@]}" start nginx >/dev/null 2>&1 || true
}
trap restore_http_proxy ERR

certbot certonly \
  --standalone \
  --non-interactive \
  --agree-tos \
  --register-unsafely-without-email \
  --domain "${PUBLIC_HOSTNAME}"

install -d -m 0755 \
  /etc/letsencrypt/renewal-hooks/pre \
  /etc/letsencrypt/renewal-hooks/post
install -m 0755 "${APP_DIR}/deploy/stop-nginx-before-renewal.sh" \
  /etc/letsencrypt/renewal-hooks/pre/actioninbox-nginx
install -m 0755 "${APP_DIR}/deploy/start-nginx-after-renewal.sh" \
  /etc/letsencrypt/renewal-hooks/post/actioninbox-nginx

systemctl enable --now certbot.timer
"${compose[@]}" up -d nginx
trap - ERR

curl --fail --silent --show-error --max-time 30 \
  "https://${PUBLIC_HOSTNAME}/health" >/dev/null
echo "HTTPS is healthy at https://${PUBLIC_HOSTNAME}"
