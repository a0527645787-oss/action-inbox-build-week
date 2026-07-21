# Emergency EC2 deployment

This directory is the runtime configuration for the temporary Build Week EC2 architecture.

- `nginx` is the only service publishing a host port.
- `actioninbox` reaches MySQL on the internal `backend` network and reaches OpenAI through the separate `frontend` network.
- `mysql` does not publish port 3306 and stores data in the persistent `actioninbox_mysql_data` named volume.
- The application runs `alembic upgrade head` before Uvicorn starts and waits for a healthy MySQL service.
- No Avast CA is mounted in AWS; normal public certificate trust is used.
- Public HTTPS uses `actioninbox.16-192-83-71.nip.io`, which resolves to the
  deployment Elastic IP, with a Let's Encrypt certificate managed by Certbot.

The instance operator supplies `/opt/actioninbox/.env` with mode `600` and invokes Compose with:

```bash
docker compose --env-file /opt/actioninbox/.env -f deploy/docker-compose.production.yml up --build -d
```

Required environment keys are `MYSQL_DATABASE`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_ROOT_PASSWORD`, and `OPENAI_API_KEY`. Never commit the real environment file.

This is an emergency single-host deployment. RDS MySQL remains the intended managed-database follow-up.

## HTTPS bootstrap

After the HTTP deployment is healthy, run the idempotent bootstrap on the EC2
host. It verifies DNS before stopping only the Nginx container, obtains the
certificate through the HTTP-01 standalone challenge, enables Certbot's renewal
timer, installs an Nginx reload hook, and verifies the HTTPS health endpoint:

```bash
sudo /opt/actioninbox/repo/deploy/provision-https.sh
```

The Google OAuth redirect URI reserved for the next stage is:

```text
https://actioninbox.16-192-83-71.nip.io/auth/google/callback
```
