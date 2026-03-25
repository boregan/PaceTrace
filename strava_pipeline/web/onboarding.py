"""
Self-serve Strava OAuth onboarding.

Routes:
  GET /connect    — landing page with "Connect with Strava" button
  GET /callback   — OAuth callback, stores tokens, shows success page
"""
from __future__ import annotations

import os
import re

import httpx
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

STRAVA_AUTH_URL = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"


def _base_url() -> str:
    domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    if domain:
        return f"https://{domain}"
    return "http://localhost:8000"


def _slugify(name: str) -> str:
    first = name.strip().split()[0] if name.strip() else "user"
    return re.sub(r"[^a-z0-9]", "", first.lower()) or "user"


def _unique_username(base: str) -> str:
    from strava_pipeline.db.tokens import username_exists
    username = base
    i = 2
    while username_exists(username):
        username = f"{base}{i}"
        i += 1
    return username


@router.get("/connect", response_class=HTMLResponse)
async def connect_page():
    client_id = os.environ.get("STRAVA_CLIENT_ID", "")
    callback_url = f"{_base_url()}/callback"
    auth_url = (
        f"{STRAVA_AUTH_URL}?client_id={client_id}"
        f"&redirect_uri={callback_url}"
        f"&response_type=code"
        f"&scope=activity:read_all"
    )
    return HTMLResponse(f"""<!DOCTYPE html>
<html>
<head>
  <title>PaceTrace — Connect Strava</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{ font-family: system-ui, -apple-system, sans-serif; max-width: 480px;
           margin: 80px auto; padding: 0 24px; color: #111; }}
    h1 {{ font-size: 1.8rem; margin-bottom: 4px; }}
    .sub {{ color: #666; margin-bottom: 32px; line-height: 1.6; }}
    .btn {{ display: inline-block; background: #fc4c02; color: #fff; padding: 14px 28px;
            border-radius: 6px; text-decoration: none; font-weight: 600; font-size: 1rem; }}
    .btn:hover {{ background: #e04400; }}
    .note {{ margin-top: 24px; font-size: 0.85rem; color: #888; }}
  </style>
</head>
<body>
  <h1>PaceTrace</h1>
  <p class="sub">
    Connect your Strava account so Claude can analyse your training — HR zones,
    pacing patterns, weekly load, and more. Read-only access only.
  </p>
  <a class="btn" href="{auth_url}">Connect with Strava</a>
  <p class="note">Your data is stored privately. We never post to Strava on your behalf.</p>
</body>
</html>""")


@router.get("/callback", response_class=HTMLResponse)
async def oauth_callback(code: str = "", error: str = ""):
    if error or not code:
        return HTMLResponse(
            f"<h1>Connection failed</h1><p>{error or 'No code returned from Strava.'}</p>",
            status_code=400,
        )

    client_id = os.environ.get("STRAVA_CLIENT_ID")
    client_secret = os.environ.get("STRAVA_CLIENT_SECRET")

    async with httpx.AsyncClient() as http:
        resp = await http.post(STRAVA_TOKEN_URL, data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
        })

    if resp.status_code != 200:
        return HTMLResponse(
            f"<h1>Token exchange failed</h1><p>{resp.text}</p>",
            status_code=400,
        )

    data = resp.json()
    athlete = data["athlete"]
    athlete_id = int(athlete["id"])
    display_name = f"{athlete.get('firstname', '')} {athlete.get('lastname', '')}".strip()

    from strava_pipeline.db.tokens import (
        upsert_athlete_tokens,
        get_tokens_by_athlete_id,
    )

    existing = get_tokens_by_athlete_id(athlete_id)
    username = existing["username"] if existing else _unique_username(_slugify(display_name))

    upsert_athlete_tokens(
        athlete_id=athlete_id,
        username=username,
        display_name=display_name,
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        token_expires_at=data["expires_at"],
    )

    base = _base_url()
    mcp_sse_url = f"{base}/mcp/sse"
    desktop_config = (
        '{{\n  "mcpServers": {{\n    "pacetrace": {{\n'
        '      "command": "npx",\n'
        f'      "args": ["-y", "mcp-remote", "{mcp_sse_url}?user={username}"]\n'
        "    }}\n  }}\n}}"
    )
    first_name = display_name.split()[0] if display_name else "there"

    return HTMLResponse(f"""<!DOCTYPE html>
<html>
<head>
  <title>PaceTrace — Connected!</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{ font-family: system-ui, -apple-system, sans-serif; max-width: 600px;
           margin: 60px auto; padding: 0 24px; color: #111; }}
    h1 {{ color: #2a7a2a; }}
    .card {{ background: #f6f6f6; border-radius: 8px; padding: 18px 22px; margin: 20px 0; }}
    pre {{ background: #1a1a1a; color: #e8e8e8; padding: 16px; border-radius: 6px;
           overflow-x: auto; font-size: 0.82rem; line-height: 1.5; white-space: pre-wrap; }}
    h3 {{ margin: 28px 0 8px; }}
    code {{ background: #eee; padding: 2px 6px; border-radius: 3px; font-size: 0.9em; }}
    .note {{ color: #888; font-size: 0.88rem; margin-top: 32px; }}
  </style>
</head>
<body>
  <h1>Connected, {first_name}!</h1>
  <p>Your Strava is linked. Historical activity data syncs automatically — new runs appear within minutes of finishing.</p>

  <div class="card">
    <strong>Your username:</strong> <code>{username}</code><br>
    <strong>Athlete ID:</strong> <code>{athlete_id}</code>
  </div>

  <h3>Option 1 — Claude Desktop</h3>
  <p>Paste into <code>~/Library/Application Support/Claude/claude_desktop_config.json</code> and restart Claude:</p>
  <pre>{desktop_config}</pre>

  <h3>Option 2 — claude.ai (browser)</h3>
  <p>Go to <strong>Settings → Integrations → Add custom integration</strong> and enter this URL:</p>
  <pre>{mcp_sse_url}?user={username}</pre>

  <h3>Then ask Claude</h3>
  <p>Try: <em>"What did I run this week?"</em> or <em>"How was my long run on Saturday?"</em></p>

  <p class="note">
    Historical data (up to 1000 activities/day) backfills automatically via a daily job.
    Your full history will be available within a few days.
  </p>
</body>
</html>""")
