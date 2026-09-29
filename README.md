# Sofía — AI Voice Scheduling Agent

A phone agent that books, cancels and reschedules medical appointments by voice, in Latin American Spanish. Patients call a regular phone number; Sofía listens, understands, checks availability and writes the appointment to the database — in real time, and without talking over the caller.

Built with **FastAPI**, **Claude (tool calling)**, **Twilio Media Streams**, **Deepgram** (streaming speech-to-text) and **Azure Neural TTS**, on **PostgreSQL** and **Redis**.

## How a call flows

```
 Caller ──phone──▶ Twilio ──POST /v1/voice/incoming──▶ FastAPI (signature validated)
                      │
                      └──WebSocket /v1/voice/stream (mu-law 8 kHz, 20 ms frames)
                                   │
            ┌──────────────────────┼──────────────────────────┐
            ▼                      ▼                          ▼
   Deepgram STT (es-419)    Claude, streaming          Azure Neural TTS
   partial + final text  ─▶ + tool calling       ─▶   (es-CO), sentence by
                               │                       sentence back to Twilio
                               ▼
               create / cancel / reschedule / list appointments,
               check availability, send reminders, fill web forms
                               │
                               ▼
                   PostgreSQL (appointments)  ·  Redis (encrypted sessions)
```

The same agent is also exposed as a text API (`POST /v1/agent/chat`) for web or WhatsApp front-ends.

## What's interesting here

**Real-time barge-in.** When the caller starts talking while Sofía is speaking, the server sends Twilio a `clear` event (so buffered audio stops immediately) *and* cancels the in-flight LLM stream and synthesis. Stopping only one of the two leaves the caller hearing Sofía for several seconds after interrupting.

**Low time-to-first-audio.** Claude's response is streamed and synthesized sentence by sentence, so audio starts with the first sentence instead of after the full answer. Chromium (for the form filler) is launched at startup, not mid-call, to avoid 1–3 s of dead air.

**Security by default.**
- JWT access tokens issued only to backends holding a client API key; a *separate* internal key guards the reminder trigger, so a compromised client can't mass-send notifications.
- Twilio webhook signatures (`X-Twilio-Signature`) validated against the configured public URL.
- Patient PII and conversation history encrypted at rest (Fernet: AES-128-CBC + HMAC-SHA256).
- Rate limiting (slowapi), explicit CORS allowlist, security headers, request audit logging, OpenAPI docs disabled in production, and a global handler that never leaks stack traces.
- Web form filling restricted to an allowlist of hosts; disabled when the list is empty.
- Container runs as a non-root user.

**Production-shaped operations.** Async SQLAlchemy with Alembic migrations run as a separate one-shot step (so replicas don't race on `upgrade head`), a `/health/ready` probe that checks Postgres and Redis, and structured JSON logs.

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/auth/token` | Issue a JWT for a user (requires a client API key) |
| `POST` | `/v1/agent/chat` | Text conversation with the agent |
| `GET` | `/v1/appointments/{user_id}` | List a user's appointments |
| `POST` | `/v1/reminders/trigger` | Send due reminders (internal key only) |
| `POST` | `/v1/voice/incoming` | Twilio voice webhook |
| `WS` | `/v1/voice/stream` | Twilio Media Stream |
| `GET` | `/health`, `/health/ready` | Liveness / readiness |
| `GET` | `/dashboard` | Operations dashboard (static page) |
| `GET` | `/dashboard/data` | Dashboard metrics as JSON (internal key only) |

Interactive docs at `/docs` when `APP_ENV` is not `production`.

## Run it

Requires Docker, plus API keys for Anthropic, and — for voice — Twilio, Deepgram and Azure Speech.

```bash
cp .env.example .env          # fill in keys and secrets
export POSTGRES_PASSWORD=change-me
docker compose up --build     # db + redis, runs migrations, then the API on :8000
```

Skip the ~700 MB Chromium layer if you don't need the form filler:

```bash
docker compose build --build-arg INSTALL_BROWSER=false
```

To take real calls, point your Twilio number's voice webhook at `https://<your-host>/v1/voice/incoming` and set `PUBLIC_BASE_URL` to that exact host.

## Operations dashboard

![Sofía operations dashboard](docs/dashboard.png)

<sub>Screenshot with sample data. Dark mode: [docs/dashboard-dark.png](docs/dashboard-dark.png).</sub>

A live view (refreshes every 5 s) to check that Sofía is working:

- **Status**: healthy / degraded, environment and uptime.
- **Calls**: active, today, total, errors today, and a 7-day bar chart.
- **Connections**: Postgres, Redis and Chromium (form filler).
- **Integrations**: LLM, Twilio, Deepgram, Azure TTS and SendGrid (configured / missing).
- **Appointments and reminders**: created today by status, next 24 h, reminders by status.

It only exposes counts and states — no patient data, no secrets. If Postgres or Redis is down, the dashboard still loads and flags the affected block instead of failing.

**Open it:**

1. Set at least one key in `INTERNAL_API_KEYS` in `.env` (the same key the reminder cron uses):
   ```bash
   INTERNAL_API_KEYS=["<long-random-key>"]
   ```
2. Start the stack with `docker compose up --build`. The image compiles the TypeScript frontend in a Node build stage, so the host doesn't need Node.
3. Go to <http://localhost:8000/dashboard> and enter the key.

The page ships no data; it fetches `GET /dashboard/data` with an `X-Internal-Key` header (401 without a valid key). Call metrics are Redis counters written off the call's hot path, so the greeting never waits on them.

**Frontend development** (without Docker):

```bash
cd dashboard
npm ci
npm run build     # writes app/api/static/dashboard.js (not versioned)
npm run watch     # rebuild on save
```

Source is `dashboard/src/dashboard.ts`; its types mirror the `/dashboard/data` JSON in `app/api/dashboard.py`.

## Tests

Tests run without network or credentials — Deepgram, Azure, Twilio and the LLM are simulated. Each file runs directly:

```bash
pip install -r requirements.txt
python tests/test_voice_stream.py      # media stream framing + barge-in
python tests/test_chat_stream.py       # first audio on first sentence, barge-in cancels LLM
python tests/test_auth_token.py        # token issuance + global rate limit
python tests/test_reminders.py         # internal-key-only trigger, explicit dispatch failures
python tests/test_browser_persistente.py  # browser reuse, session isolation, crash recovery
python tests/test_dashboard.py         # CSP, internal key required, degraded instead of 500
```

## Stack

Python 3.11 · FastAPI · Pydantic v2 · Anthropic SDK · SQLAlchemy (async) + asyncpg · Alembic · Redis · PyJWT · cryptography · slowapi · Playwright · structlog · Twilio · SendGrid · TypeScript · Docker

## CI/CD (VPS)

`.github/workflows/ci.yml`: every PR runs tests + dashboard build + `docker build`. On `main` the image is pushed to GHCR as `ghcr.io/f3rnancaviedes/callcenteragent_fastapi:<sha>` and deployed to the VPS over SSH. All actions are pinned by commit SHA; Dependabot keeps them current.

One-time VPS setup:

```bash
# 1. Deploy user (docker group = root-equivalent; the key restriction below is what limits it)
sudo useradd -m -s /bin/sh -G docker deploy
sudo mkdir -p /opt/sofia && sudo chown deploy: /opt/sofia
# 2. Copy docker-compose.yml, deploy/deploy.sh and a production .env to /opt/sofia (chmod 600 .env)
# 3. If the GHCR package is private: as deploy, `docker login ghcr.io` with a PAT scoped read:packages
# 4. Key pair for CI; the public key can only run deploy.sh:
ssh-keygen -t ed25519 -N "" -f ci-deploy
sudo -u deploy install -m 700 -d ~deploy/.ssh
echo "command=\"/opt/sofia/deploy.sh\",restrict $(cat ci-deploy.pub)" | sudo tee -a ~deploy/.ssh/authorized_keys
```

GitHub → Settings → Environments → `production` (add required reviewers if you want manual approval), secrets:

| Secret | Value |
|---|---|
| `VPS_HOST` | VPS hostname/IP |
| `VPS_USER` | `deploy` |
| `VPS_SSH_KEY` | contents of `ci-deploy` (private key) |
| `VPS_KNOWN_HOSTS` | output of `ssh-keyscan <host>`, verified against the VPS fingerprint |

Rollback: `ssh -i ci-deploy deploy@<host> <previous-sha>`. Put a TLS reverse proxy (Caddy/nginx) in front of port 8000.
