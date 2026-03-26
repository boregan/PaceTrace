"""
intervals.icu onboarding — simple API key connection page for PaceTrace v2.
"""

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
import httpx

from ..db.users import upsert_user

router = APIRouter()


CONNECT_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>PaceTrace — Setup</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, system-ui, sans-serif; background: #0a0a0a; color: #e0e0e0; min-height: 100vh; padding: 40px 20px; }
        .container { max-width: 600px; margin: 0 auto; }
        h1 { font-size: 28px; margin-bottom: 4px; color: #fff; }
        .subtitle { color: #888; margin-bottom: 40px; font-size: 15px; }
        .phase { background: #1a1a1a; border: 1px solid #333; border-radius: 16px; padding: 32px; margin-bottom: 24px; }
        .phase-header { display: flex; align-items: center; gap: 12px; margin-bottom: 20px; }
        .phase-num { background: #ff4500; color: #fff; width: 28px; height: 28px; border-radius: 50%; text-align: center; line-height: 28px; font-size: 13px; font-weight: bold; flex-shrink: 0; }
        .phase-title { font-size: 18px; font-weight: 600; color: #fff; }
        .phase-desc { color: #999; font-size: 14px; line-height: 1.7; margin-bottom: 16px; }
        .phase-desc a { color: #ff4500; text-decoration: none; }
        .phase-desc a:hover { text-decoration: underline; }
        .substep { padding: 8px 0 8px 16px; border-left: 2px solid #333; margin: 8px 0; color: #ccc; font-size: 14px; line-height: 1.6; }
        .substep a { color: #ff4500; text-decoration: none; }
        .time { display: inline-block; background: #1a2a1a; color: #4caf50; font-size: 11px; padding: 2px 8px; border-radius: 4px; margin-left: 8px; }
        .optional { display: inline-block; background: #2a2a1a; color: #ff9800; font-size: 11px; padding: 2px 8px; border-radius: 4px; margin-left: 8px; }
        label { display: block; font-size: 13px; color: #999; margin-bottom: 6px; }
        input[type=text], input[type=password] { width: 100%; padding: 12px; background: #111; border: 1px solid #333; border-radius: 8px; color: #fff; font-size: 14px; font-family: monospace; }
        input:focus { outline: none; border-color: #ff4500; }
        .fields { display: flex; flex-direction: column; gap: 16px; margin: 16px 0; }
        button { width: 100%; padding: 14px; background: #ff4500; color: #fff; border: none; border-radius: 8px; font-size: 16px; font-weight: 600; cursor: pointer; }
        button:hover { background: #e03d00; }
        .note { background: #111; border: 1px solid #333; border-radius: 8px; padding: 14px; font-size: 13px; color: #888; line-height: 1.6; margin-top: 12px; }
        .error { background: #2a1010; border: 1px solid #ff3333; color: #ff6666; }
        code { background: #111; padding: 2px 6px; border-radius: 4px; font-size: 13px; color: #ccc; }
    </style>
</head>
<body>
<div class="container">
    <h1>PaceTrace</h1>
    <p class="subtitle">Get set up in about 5 minutes. You'll need a free intervals.icu account.</p>

    <!-- Phase 1: Create intervals.icu -->
    <div class="phase">
        <div class="phase-header">
            <span class="phase-num">1</span>
            <span class="phase-title">Create your intervals.icu account</span>
            <span class="time">2 min</span>
        </div>
        <div class="phase-desc">
            <a href="https://intervals.icu" target="_blank">intervals.icu</a> is a free training platform
            that PaceTrace uses under the hood. It connects to your watch and computes all the
            fitness metrics (CTL, ATL, TSB, training load, zones, etc).
        </div>
        <div class="substep">
            Go to <a href="https://intervals.icu" target="_blank">intervals.icu</a> and sign up (free)
        </div>
        <div class="substep">
            Connect your <b>Garmin / Wahoo / Polar / Suunto / COROS</b> watch — this gives PaceTrace
            your HRV, sleep, resting HR, and wellness data
        </div>
        <div class="substep">
            Connect <b>Strava</b> too — this syncs your runs going forward
        </div>
    </div>

    <!-- Phase 2: Import history -->
    <div class="phase">
        <div class="phase-header">
            <span class="phase-num">2</span>
            <span class="phase-title">Import your run history</span>
            <span class="optional">optional</span>
        </div>
        <div class="phase-desc">
            intervals.icu free tier only syncs <b>going forward</b>. To get your full Strava history
            (so PaceTrace can show fitness trends, pace progression, etc), do a one-time Strava export:
        </div>
        <div class="substep">
            Go to <a href="https://www.strava.com/athlete/delete_your_account" target="_blank">Strava → Settings → "Download or Delete Your Account"</a>
        </div>
        <div class="substep">
            Click <b>"Request Your Archive"</b> — Strava emails you a ZIP of all your activities (takes a few minutes)
        </div>
        <div class="substep">
            Once downloaded, run our backfill script:<br>
            <code>python scripts/backfill_intervals.py --zip ~/Downloads/export.zip --user YOUR_USERNAME</code><br>
            This uploads all your historical runs to intervals.icu automatically.
        </div>
        <div class="note">
            Don't worry if you skip this — PaceTrace works fine without history, it just
            means fitness trends start from when you connected. Your history builds over time.
        </div>
    </div>

    <!-- Phase 3: Connect to PaceTrace -->
    <div class="phase">
        <div class="phase-header">
            <span class="phase-num">3</span>
            <span class="phase-title">Connect to PaceTrace</span>
            <span class="time">1 min</span>
        </div>
        <div class="phase-desc">
            Get your API key from intervals.icu so PaceTrace can read your data:
        </div>
        <div class="substep">
            Go to <a href="https://intervals.icu/settings" target="_blank">intervals.icu/settings</a> → scroll to <b>Developer Settings</b>
        </div>
        <div class="substep">
            Click <b>Generate</b> to create your API key, then paste it below
        </div>

        <form method="POST" action="/v2/connect">
            <div class="fields">
                <div>
                    <label>Your name</label>
                    <input type="text" name="display_name" placeholder="e.g. Ben" required>
                </div>
                <div>
                    <label>intervals.icu API Key</label>
                    <input type="password" name="api_key" placeholder="Paste your API key" required>
                </div>
                <div>
                    <label>Athlete ID (leave blank to auto-detect)</label>
                    <input type="text" name="athlete_id" placeholder="e.g. i12345">
                </div>
            </div>
            <button type="submit">Connect to PaceTrace</button>
        </form>

        <div class="note">
            Your API key is stored securely and only used to fetch your training data.
            You can revoke it anytime from intervals.icu settings.
        </div>
    </div>
</div>
</body>
</html>
"""


SUCCESS_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>PaceTrace v2 — Connected!</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: -apple-system, system-ui, sans-serif; background: #0a0a0a; color: #e0e0e0; min-height: 100vh; display: flex; align-items: center; justify-content: center; }}
        .card {{ background: #1a1a1a; border: 1px solid #333; border-radius: 16px; padding: 40px; max-width: 520px; width: 90%; }}
        h1 {{ font-size: 24px; margin-bottom: 8px; color: #fff; }}
        .success {{ color: #4caf50; font-size: 14px; margin-bottom: 24px; }}
        .config {{ background: #111; border: 1px solid #333; border-radius: 8px; padding: 16px; margin: 16px 0; font-family: monospace; font-size: 12px; color: #ccc; white-space: pre-wrap; word-break: break-all; }}
        .section {{ margin-bottom: 24px; }}
        .section h3 {{ font-size: 14px; color: #999; margin-bottom: 8px; }}
        .label {{ font-size: 13px; color: #888; margin-bottom: 4px; }}
        a {{ color: #ff4500; text-decoration: none; }}
    </style>
</head>
<body>
<div class="card">
    <h1>You're connected! </h1>
    <p class="success">Welcome, {display_name}. Your intervals.icu account is linked.</p>

    <div class="section">
        <h3>Your username</h3>
        <div class="config">{username}</div>
    </div>

    <div class="section">
        <h3>Connect to Claude (browser)</h3>
        <p class="label">Add this URL as an MCP integration in Claude.ai settings:</p>
        <div class="config">{base_url}/v2/mcp/sse?user={username}</div>
    </div>

    <div class="section">
        <h3>Connect to Claude Desktop</h3>
        <div class="config">{{
  "mcpServers": {{
    "pacetrace-v2": {{
      "command": "python",
      "args": ["{mcp_path}"],
      "env": {{
        "PACETRACE_USER": "{username}",
        "PACETRACE_VERSION": "v2"
      }}
    }}
  }}
}}</div>
    </div>
</div>
</body>
</html>
"""


@router.get("/v2/connect", response_class=HTMLResponse)
async def connect_page():
    return HTMLResponse(CONNECT_HTML)


@router.post("/v2/connect", response_class=HTMLResponse)
async def connect_submit(
    request: Request,
    display_name: str = Form(...),
    api_key: str = Form(...),
    athlete_id: str = Form(""),
):
    # Validate the API key by calling intervals.icu
    try:
        icu_athlete_id = athlete_id.strip() or "0"
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.get(
                f"https://intervals.icu/api/v1/athlete/{icu_athlete_id}",
                auth=httpx.BasicAuth("API_KEY", api_key.strip()),
            )
            resp.raise_for_status()
            profile = resp.json()
    except httpx.HTTPStatusError as e:
        return HTMLResponse(
            CONNECT_HTML.replace(
                "</form>",
                '<div class="info error">Invalid API key or athlete ID. Please check and try again.</div></form>',
            ),
            status_code=400,
        )
    except Exception as e:
        return HTMLResponse(
            CONNECT_HTML.replace(
                "</form>",
                f'<div class="info error">Connection error: {e}</div></form>',
            ),
            status_code=500,
        )

    # Extract athlete info
    real_athlete_id = str(profile.get("id", icu_athlete_id))
    name = profile.get("name", display_name)

    # Generate username slug
    slug = display_name.strip().lower().replace(" ", "-")
    slug = "".join(c for c in slug if c.isalnum() or c == "-")
    if not slug:
        slug = f"athlete-{real_athlete_id}"

    # Save user
    user = upsert_user(
        username=slug,
        display_name=name or display_name,
        icu_athlete_id=real_athlete_id,
        icu_api_key=api_key.strip(),
    )

    # Build success page
    base_url = str(request.base_url).rstrip("/")
    mcp_path = "/app/mcp_server_v2.py"  # Railway path

    html = SUCCESS_HTML.format(
        display_name=name or display_name,
        username=slug,
        base_url=base_url,
        mcp_path=mcp_path,
    )
    return HTMLResponse(html)
