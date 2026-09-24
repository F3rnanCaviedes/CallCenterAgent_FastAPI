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

## Tests

Tests run without network or credentials — Deepgram, Azure, Twilio and the LLM are simulated. Each file runs directly:

```bash
pip install -r requirements.txt
python tests/test_voice_stream.py      # media stream framing + barge-in
python tests/test_chat_stream.py       # first audio on first sentence, barge-in cancels LLM
python tests/test_auth_token.py        # token issuance + global rate limit
python tests/test_reminders.py         # internal-key-only trigger, explicit dispatch failures
python tests/test_browser_persistente.py  # browser reuse, session isolation, crash recovery
```

## Stack

Python 3.11 · FastAPI · Pydantic v2 · Anthropic SDK · SQLAlchemy (async) + asyncpg · Alembic · Redis · PyJWT · cryptography · slowapi · Playwright · structlog · Twilio · SendGrid · Docker
