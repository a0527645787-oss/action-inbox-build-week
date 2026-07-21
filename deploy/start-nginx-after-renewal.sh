#!/usr/bin/env bash
set -euo pipefail

cd /opt/actioninbox/repo
docker compose \
  --env-file /opt/actioninbox/.env \
  -f deploy/docker-compose.production.yml \
  up -d nginx
