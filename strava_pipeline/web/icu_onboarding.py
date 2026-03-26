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
    <title>PaceTrace v2 — Connect intervals.icu</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, system-ui, sans-serif; background: #0a0a0a; color: #e0e0e0; min-height: 100vh; display: flex; align-items: center; justify-content: center; }
        .card { background: #1a1a1a; border: 1px solid #333; border-radius: 16px; padding: 40px; max-width: 520px; width: 90%; }
        h1 { font-size: 24px; margin-bottom: 8px; color: #fff; }
        .subtitle { color: #888; margin-bottom: 32px; font-size: 14px; }
        .step { margin-bottom: 24px; }
        .step-num { display: inline-block; background: #ff4500; color: #fff; width: 24px; height: 24px; border-radius: 50%; text-align: center; line-height: 24px; font-size: 12px; font-weight: bold; margin-right: 8px; }
        .step-text { color: #ccc; font-size: 14px; line-height: 1.6; }
        .step-text a { color: #ff4500; text-decoration: none; }
        .step-text a:hover { text-decoration: underline; }
        label { display: block; font-size: 13px; color: #999; margin-bottom: 6px; }
        input[type=text], input[type=password] { width: 100%; padding: 12px; background: #111; border: 1px solid #333; border-radius: 8px; color: #fff; font-size: 14px; font-family: monospace; }
        input:focus { outline: none; border-color: #ff4500; }
        .fields { display: flex; flex-direction: column; gap: 16px; margin: 24px 0; }
        button { width: 100%; padding: 14px; background: #ff4500; color: #fff; border: none; border-radius: 8px; font-size: 16px; font-weight: 600; cursor: pointer; }
        button:hover { background: #e03d00; }
        .info { background: #111; border: 1px solid #333; border-radius: 8px; padding: 16px; margin-top: 24px; font-size: 13px; color: #999; line-height: 1.6; }
        .error { background: #2a1010; border: 1px solid #ff3333; color: #ff6666; }
    </style>
</head>
<body>
<div class="card">
    <h1>PaceTrace v2</h1>
    <p class="subtitle">Connect your intervals.icu account to unlock AI training analysis</p>

    <div class="step">
        <span class="step-num">1</span>
        <span class="step-text">Go to <a href="https://intervals.icu/settings" target="_blank">intervals.icu/settings</a> and scroll to <b>Developer Settings</b></span>
    </div>
    <div class="step">
        <span class="step-num">2</span>
        <span class="step-text">Click <b>Generate</b> to create your API key</span>
    </div>
    <div class="step">
        <span class="step-num">3</span>
        <span class="step-text">Copy the key and your Athlete ID (bottom of settings page), then paste below</span>
    </div>

    <form method="POST" action="/v2/connect">
        <div class="fields">
            <div>
                <label>Your name (for your profile)</label>
                <input type="text" name="display_name" placeholder="e.g. Ben" required>
            </div>
            <div>
                <label>intervals.icu API Key</label>
                <input type="password" name="api_key" placeholder="Paste your API key" required>
            </div>
            <div>
                <label>intervals.icu Athlete ID (optional — we'll auto-detect if blank)</label>
                <input type="text" name="athlete_id" placeholder="e.g. i12345 (leave blank to auto-detect)">
            </div>
        </div>
        <button type="submit">Connect to PaceTrace</button>
    </form>

    <div class="info">
        Your API key is stored securely and only used to fetch your training data.
        You can revoke it anytime from intervals.icu settings.
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
