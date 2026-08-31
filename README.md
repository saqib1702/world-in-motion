# World in Motion

A live multi-agent geopolitical simulation. Ten real-world actors are each played
by a language-model agent that reads current headlines, reasons in character, and
adjusts its stance toward the other nine. The resulting relation matrix renders as
a 3D force-directed graph that moves as the world moves.

> **Simulated projections — not real policy.** The nations are real, the agents are
> not. Every statement and relation shift is a language model's inference from a
> news headline, and represents no government's actual position. The UI carries
> this disclaimer permanently, and mock output produced when Gemini is unavailable
> is prefixed `[DEMO MODE` so fabricated reasoning can never be mistaken for live
> reasoning.

## What it actually does

Each tick:

1. **Ingest.** `ingestion/fetcher.py` pulls from GDELT and Google News, then
   entity-matches every article against the modelled actors — an alias index with
   word-boundary matching, plus a geopolitical-verb filter, so "Turkey" the country
   is not confused with the bird and a celebrity story naming a country is dropped.
   Events are deduplicated by a SHA-1 of the source URL, so the world never reacts
   twice to the same article.
2. **Perceive.** Every agent records the tick's events into its own memory log.
3. **Deliberate.** All ten actors decide inside a **single** structured Gemini call
   (`engine/deliberation.py`). This is the difference between one request per tick
   and ten — decisive on a free-tier key limited to 5 requests per minute. A
   malformed batch response degrades to per-actor calls automatically.
4. **Commit.** Each decision produces an action, a reasoning string, and a relation
   delta, written to MongoDB and broadcast over Socket.IO. The 3D graph animates the
   change without a page reload.

The ten actors are the United States, China, Russia, the European Union, India,
Japan, the United Kingdom, Brazil, Turkey, and the Gulf States. Relations are
**directed** — how Washington sees Beijing is a separate row from how Beijing sees
Washington — which is why a ten-actor world has 90 relation rows, rendered as 45
edges coloured by the mean of each pair.

## Stack

| Layer | Choice |
| --- | --- |
| Backend | Flask + Flask-SocketIO (`async_mode="threading"`) |
| Database | MongoDB Atlas |
| Frontend | React 18 + Vite |
| 3D | react-three-fiber **8.x** + drei + three.js |
| Layout | d3-force-3d, stepped manually from the render loop |
| LLM | Gemini via `google-genai`, structured output |

`@react-three/fiber` is pinned to 8.x for React 18 compatibility. Do not upgrade
without checking — v9 requires React 19.

## Running it locally

Two terminals. Backend first.

```bash
# 1. Backend (repo root)
python -m venv .venv
.venv\Scripts\activate         # Windows
# source .venv/bin/activate    # macOS / Linux
pip install -r requirements.txt

cp .env.example .env           # then fill in MONGO_URI and GEMINI_API_KEY

python scripts/check_connections.py   # verify Atlas + Gemini before starting
python -m db.seed                     # one-time: create the ten actors
python app.py                         # http://127.0.0.1:5000
```

```bash
# 2. Frontend
cd frontend
npm install
npm run dev                           # http://127.0.0.1:5173
```

Open the Vite URL, not the Flask one — in development Vite serves the UI and
proxies API calls to Flask. (In production one container serves both.)

### The commands that matter

| Command | What it does |
| --- | --- |
| `python scripts/check_connections.py` | Live preflight: Atlas reachable, collections seeded, relation ids canonical, Gemini key calling a real model. Run this first when something looks wrong. |
| `python -m db.seed` | Seeds the ten actors and the 90 directed relations. Idempotent; also purges agents outside the current roster. |
| `python app.py` | Development server, with the reloader disabled (see below). |
| `python -m tests.run_all` | All five offline regression suites (199 assertions). Plain scripts, no pytest — each returns an exit code, and each runs in its own subprocess so one suite's fakes cannot leak into the next. Run before committing. |
| `npm run build` (in `frontend/`) | Production bundle into `frontend/dist`. |

Set `ENABLE_SCHEDULED_INGESTION=true` in `.env` for a continuous run that fetches
news every `EVENT_FETCH_INTERVAL_MINUTES`. It is off by default because every tick
spends Gemini tokens. Without it, use the **Trigger** panel in the UI to inject a
headline and watch one tick resolve.

### Verification comes in two halves

Worth being precise about, because passing one half proves nothing about the other.

`python -m tests.run_all` is the **offline** half. It stubs out pymongo and
`google.genai`, so it can check logic that would otherwise need a live account:
that every relation row is keyed by a canonical `agent_id`, that the entity matcher
routes "US and China agree to pause tariff escalation" to both actors while dropping
"Turkey recipes for Thanksgiving", that the rate limiter and model fallback behave
under refusal, and that a malformed batch response degrades to per-actor calls. It
runs in a couple of seconds and needs no credentials.

The security suite is part of that half but works differently: it drives real HTTP
through `app.test_client()`, so it exercises the actual routing table and the real
decorator stack in its real order. A route that quietly loses its `@require_api_token`
fails there, which a unit test of the decorator would not catch.

`python scripts/check_connections.py` is the **live** half — the only thing that
proves Atlas accepts your URI and your Gemini key can call the configured model. It
deliberately inspects the reply for the `[DEMO MODE` prefix, because `generate()`
falls back to a mock generator rather than raising: a call that looks successful can
still mean no real reasoning happened.

## Security

The threat model is narrow and specific: two endpoints spend money on every call.
`POST /engine/tick` runs a batched Gemini deliberation across the whole roster, and
`POST /agents/<id>/chat` runs one Gemini call per message. Deployed with no gate, a
single `curl` loop drains a free-tier quota in seconds and fills the Atlas cluster.
So: **reads are public, writes need a token.** The demo stays linkable; the bill
stays bounded.

| Control | Where | Note |
| --- | --- | --- |
| Bearer / `X-API-Key` token on writes | `api/security.py` | Compared with `hmac.compare_digest`; `==` short-circuits on the first wrong byte and leaks the prefix through timing. |
| Fail closed in production | `require_api_token` | No `API_TOKEN` + `FLASK_ENV=production` → 503 with the fix in the message, not open access. In development it allows the write and logs a warning. |
| Per-IP sliding-window rate limits | `@rate_limit` | Separate buckets per endpoint group, so a write flood cannot starve reads. Distinct from the Gemini RPM limiter, which paces *outbound* calls — that one turns a cost problem into a latency problem, this one refuses the flood at the door. |
| Request body cap | `MAX_REQUEST_BYTES` | 413 before Flask buffers the body. |
| Input sanitising | `_clean_text` / `_sanitize_event` | Control characters stripped and lengths bounded, so caller text cannot smuggle a fake `System:` line into a prompt. Unknown keys, `$where` and `__proto__` are dropped rather than stored. |
| Security headers + CSP | `after_request` | `nosniff`, `X-Frame-Options: DENY`, a strict `Referrer-Policy`, and a CSP with no `script-src` `unsafe-inline`. HSTS **only** in production — cached on a dev box it pins `localhost` to HTTPS. |
| Terse errors | 500 handler, `/health` | No stack traces, driver version strings, or Mongo URI fragments in a response. Detail goes to the log. |
| Debugger interlock | `app.py` | The Werkzeug console is remote code execution. It is enabled only when the app is both in development *and* bound to loopback, so `FLASK_HOST=0.0.0.0` for phone testing cannot expose a Python shell to the wifi. |

Generate a token with `python -c "import secrets; print(secrets.token_urlsafe(32))"`
and set `API_TOKEN`. The UI reads `GET /meta` on load to learn whether writes will
be accepted, and renders read-only instead of offering a button that returns 401.
`VITE_API_TOKEN` exists so a personal deployment can drive the controls, but note
that anything Vite inlines is public — leave it unset on a genuinely public link.

`python -m tests.test_security` covers all of the above (60 assertions).

## Deployment

`docker build -t world-in-motion .` produces a single image: stage one runs
`npm ci && npm run build` on Linux, stage two serves the built assets and the API
from one gunicorn process on port 8080.

```bash
docker build -t world-in-motion .
docker run --rm -p 8080:8080 --env-file .env world-in-motion
```

For AWS, push to ECR and run on ECS Fargate behind an ALB:

```bash
aws ecr create-repository --repository-name world-in-motion
aws ecr get-login-password --region <region> \
  | docker login --username AWS --password-stdin <acct>.dkr.ecr.<region>.amazonaws.com
docker tag world-in-motion <acct>.dkr.ecr.<region>.amazonaws.com/world-in-motion:latest
docker push <acct>.dkr.ecr.<region>.amazonaws.com/world-in-motion:latest
```

Five things to get right in the task definition:

- **Secrets are injected, never baked.** `MONGO_URI`, `GEMINI_API_KEY` and
  `API_TOKEN` belong in Secrets Manager or SSM Parameter Store, referenced from the
  task definition's `secrets` block rather than `environment`. `.env` is gitignored
  and dockerignored.
- **`FLASK_ENV=production` and a real `API_TOKEN`.** Together these are what close
  the write endpoints. Setting the first without the second is not a mistake that
  fails silently — writes return 503 until the token exists — but it does mean a
  deployment nobody can tick. Set `SOCKETIO_CORS_ORIGINS` to the ALB or domain
  origin at the same time; leaving it `*` lets any page on the internet open a
  socket to the server.
- **One worker.** See the long comment in `gunicorn.conf.py`: Socket.IO sessions
  live in process memory, so a second worker without sticky sessions or a Redis
  message queue breaks the realtime transport. Concurrency comes from threads.
- **Atlas network access.** Allow the NAT gateway's egress IP, or use a VPC peering
  connection. A Fargate task with a rotating public IP will fail the handshake.
- **Health check** `/health`, which returns 503 rather than 200 when Mongo is
  unreachable, so an ALB target group can act on it directly.

## Notes on four fixed bugs

Keeping these here because each looked like something it wasn't.

**Atlas SSL handshake failures on Windows.** Not a TLS problem. Flask's debug
reloader spawns a second process, and two processes sharing one `MongoClient`
fork-unsafely produced handshake errors. Fixed with `use_reloader=False` in
`app.py` — which is why the dev server does not auto-restart on save.

**`ECONNABORTED` from the Vite dev proxy.** Two causes, both fixed. The proxy
targeted `localhost`, which on Node 17+ resolves to `::1` first while Flask binds
IPv4 only, so the first connection attempt hit a closed port —
`vite.config.js` now pins `127.0.0.1`. Separately, the client opened directly onto
the `websocket` transport; it now starts on `polling` and upgrades, so a backend
that is still booting degrades instead of failing.

**`target_agent_id` holding a display name instead of an `agent_id`.** Root-caused
in `agents/nation.py` rather than patched downstream. Note that two different
keyings are intentional and should not be "unified": `persona.relations` is keyed
by **display name** because it renders into prompts, while the `relations`
collection is keyed by canonical **`agent_id`**. `scripts/check_connections.py`
asserts the latter on every run so the regression cannot come back quietly.

**The graph never sat still, and was slow on mobile.** The force simulation and the
relation pulse were driven through React state, so every physics step re-rendered
~100 components and drei rebuilt each line's geometry. The rule now, stated at the
top of `RelationsGraph.jsx`: React owns the *structure*, three.js objects are
mutated directly for *motion*, and nothing calls `setState` per frame. Separately,
the safety-net poll was calling `setRelations()` with a fresh array every six
seconds, restarting the simulation forever; content signatures in `App.jsx` make
an unchanged payload a no-op.

## Layout

```
agents/      NationAgent — persona, perception, memory, in-character speech
api/         Flask factory, HTTP routes, Socket.IO realtime
db/          Mongo connection, schema + indexes, helpers, seed
engine/      tick loop and batched deliberation
ingestion/   GDELT / Google News fetch, entity matching, scheduler
llm/         Gemini client, rate limiter, model fallback, mock generator
scripts/     check_connections.py, migrate_relation_ids.py
tests/       offline regression scripts (stdlib only, no pytest) — `run_all.py`
frontend/    React + Vite + react-three-fiber
```
