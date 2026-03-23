# Strava Pipeline

Pulls full time-series (stream) data from the Strava API for every run activity — historical backfill and ongoing sync — and stores it in Supabase so Claude can query it without manual file uploads.

## What it does

- **Backfill**: fetches all historical run activities + per-second stream data (HR, pace, altitude, cadence) into Supabase
- **Webhook**: a FastAPI server that auto-syncs new activities as you log them
- **Query helper**: downsamples stream data into compact summaries for Claude's context window

## Prerequisites

- Python 3.11+
- A [Strava API application](https://www.strava.com/settings/api)
- A [Supabase](https://supabase.com) project (free tier is fine)

---

## Setup

### 1. Install dependencies

```bash
cd strava-pipeline
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` with your values:

```env
STRAVA_CLIENT_ID=your_client_id
STRAVA_CLIENT_SECRET=your_client_secret
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=your_service_role_key
STRAVA_WEBHOOK_VERIFY_TOKEN=pick_a_random_string
```

### 3. Create Supabase tables

Run both migration files in the Supabase SQL editor (or via the CLI):

```
supabase/migrations/001_create_activities.sql
supabase/migrations/002_create_streams.sql
```

### 4. Authorise your Strava account

```bash
python scripts/backfill.py auth --user ben
```

This opens a browser, walks through OAuth, and saves tokens to `config/users/ben.env`.

### 5. Run the backfill

```bash
python scripts/backfill.py run --user ben
```

For ~650 activities this will take multiple runs across several days due to Strava's rate limits (100 req/15 min, 1000/day). The script is resumable — re-run it and it picks up where it left off.

**Monitor progress:**

```bash
python scripts/backfill.py status
```

**Options:**

```bash
# Only fetch activities after a date (Unix timestamp)
python scripts/backfill.py run --user ben --after 1700000000

# Only fill missing stream data (skip activity list fetch)
python scripts/backfill.py run --user ben --streams-only

# Preview without writing to DB
python scripts/backfill.py run --user ben --dry-run
```

---

## Webhook (ongoing sync)

### Deploy to Railway

1. Push this repo to GitHub
2. Create a new Railway project → Deploy from GitHub repo
3. Set all environment variables from `.env` in the Railway dashboard
4. Add `config/users/ben.env` contents as individual env vars OR use Railway's file mount feature

### Register the webhook

After deploying:

```bash
WEBHOOK_URL=https://your-app.railway.app python scripts/setup_webhook.py
```

Strava will now POST to your server whenever you log a new run.

### Local testing with ngrok

```bash
uvicorn strava_pipeline.webhook.app:app --reload
# In another terminal:
ngrok http 8000
WEBHOOK_URL=https://xxxx.ngrok.io python scripts/setup_webhook.py
```

---

## Query helper (for Claude)

Get a Claude-ready summary of any activity:

```bash
python scripts/backfill.py summary 12345678901
```

Or from Python:

```python
from strava_pipeline.claude.query_helper import build_context, build_context_json

# Text format — paste directly into a Claude prompt
text = build_context(activity_id=12345678901, max_points=120, max_hr=185)

# Structured dict — for API usage
data = build_context_json(activity_id=12345678901)
```

`max_points=120` means one sample per ~30 seconds for a 1-hour run — enough to see the shape of the session without flooding Claude's context.

---

## Adding a second user (e.g. your brother)

```bash
python scripts/backfill.py auth --user alex
python scripts/backfill.py run --user alex
```

Each user gets their own token file in `config/users/`. The webhook routes events by `athlete_id` automatically.

---

## Rate limits

Strava allows:
- **100 requests per 15 minutes**
- **1000 requests per day**

Each activity needs 1 request for its stream data. For 650 activities that's ~2 day-windows to backfill. The rate limiter handles this automatically — just re-run the script each day until done.

Current usage:

```bash
python scripts/backfill.py status
```

---

## Project structure

```
strava-pipeline/
├── .env.example                  # environment variable template
├── railway.toml                  # Railway deployment config
├── requirements.txt
├── config/users/                 # per-user token files (gitignored)
├── scripts/
│   ├── backfill.py               # CLI: auth, run, status, summary
│   └── setup_webhook.py          # one-time webhook registration
├── strava_pipeline/
│   ├── auth/
│   │   ├── token_manager.py      # OAuth2 token refresh
│   │   └── oauth_flow.py         # one-time OAuth setup
│   ├── backfill/
│   │   ├── runner.py             # orchestrator
│   │   ├── activity_fetcher.py   # fetch + store activity list
│   │   └── stream_fetcher.py     # fetch + store stream data
│   ├── db/
│   │   ├── client.py             # Supabase singleton
│   │   ├── activities.py         # activities table operations
│   │   └── streams.py            # streams table operations
│   ├── webhook/
│   │   ├── app.py                # FastAPI app
│   │   └── handlers.py           # GET/POST /webhook handlers
│   ├── claude/
│   │   └── query_helper.py       # build_context() for LLM prompts
│   └── utils/
│       ├── rate_limiter.py       # 15-min + daily request throttling
│       └── user_loader.py        # scans config/users/ directory
└── supabase/migrations/
    ├── 001_create_activities.sql
    └── 002_create_streams.sql
```
