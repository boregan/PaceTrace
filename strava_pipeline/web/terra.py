"""
Terra wearable connect flow.

Routes:
  GET /terra/connect?user=<username>   — generate widget session, redirect to Terra
  GET /terra/success                   — OAuth success landing page
  GET /terra/failure                   — OAuth failure landing page

The flow:
  1. Athlete visits /terra/connect?user=ben
  2. We generate a Terra widget session (15-min expiry)
  3. Athlete is redirected to Terra's hosted page — picks their device, logs in
  4. Terra redirects to /terra/success?user_id=...&reference_id=...&resource=GARMIN
  5. We store the terra_user_id → athlete_id mapping
  6. Terra also fires an 'auth' webhook (belt + suspenders)
"""
from __future__ import annotations

import os

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

from strava_pipeline.db.terra_users import (
    get_athlete_id_by_reference,
    upsert_terra_user,
)
from strava_pipeline.enrichment.terra import generate_widget_session

router = APIRouter()


def _base_url() -> str:
    domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    return f"https://{domain}" if domain else "http://localhost:8000"


@router.get("/terra/connect")
def terra_connect(user: str = ""):
    """
    Redirect athlete to Terra's hosted connect widget.
    Pass ?user=<username> to associate the connection with an athlete.
    """
    if not user:
        return HTMLResponse(
            "<h2>Missing ?user= parameter</h2>"
            "<p>Example: <code>/terra/connect?user=ben</code></p>",
            status_code=400,
        )

    base = _base_url()
    try:
        widget_url = generate_widget_session(
            reference_id=user,
            success_url=f"{base}/terra/success",
            failure_url=f"{base}/terra/failure",
        )
        return RedirectResponse(url=widget_url, status_code=302)
    except Exception as e:
        return HTMLResponse(
            f"<h2>Terra connection error</h2>"
            f"<p>Could not generate widget session. Check TERRA_DEV_ID / TERRA_API_KEY env vars.</p>"
            f"<pre>{e}</pre>",
            status_code=500,
        )


@router.get("/terra/success", response_class=HTMLResponse)
def terra_success(user_id: str = "", reference_id: str = "", resource: str = ""):
    """
    Terra redirects here after a user successfully connects their device.
    Stores the terra_user_id → athlete_id mapping in case the webhook is delayed.
    """
    if user_id and reference_id:
        athlete_id = get_athlete_id_by_reference(reference_id)
        if athlete_id:
            upsert_terra_user(athlete_id, user_id, resource, reference_id)

    device_name = resource.replace("_", " ").title() if resource else "device"

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PaceTrace — Connected</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f9fafb;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      padding: 24px;
    }}
    .card {{
      background: white;
      border-radius: 16px;
      padding: 48px 40px;
      max-width: 420px;
      width: 100%;
      text-align: center;
      box-shadow: 0 4px 24px rgba(0,0,0,0.08);
    }}
    .icon {{ font-size: 48px; margin-bottom: 20px; }}
    h1 {{ font-size: 24px; color: #111; margin-bottom: 12px; }}
    p {{ color: #6b7280; line-height: 1.6; margin-bottom: 10px; font-size: 15px; }}
    .device {{ color: #16a34a; font-weight: 600; }}
    .note {{ font-size: 13px; color: #9ca3af; margin-top: 24px; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">✓</div>
    <h1>You're connected!</h1>
    <p>Your <span class="device">{device_name}</span> is now linked to PaceTrace.</p>
    <p>Sleep, HRV, and recovery data will start syncing automatically.
       Historical data will be available within a few minutes.</p>
    <p class="note">You can close this tab.</p>
  </div>
</body>
</html>""")


@router.get("/terra/failure", response_class=HTMLResponse)
def terra_failure():
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PaceTrace — Connection Failed</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f9fafb;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      padding: 24px;
    }
    .card {
      background: white;
      border-radius: 16px;
      padding: 48px 40px;
      max-width: 420px;
      width: 100%;
      text-align: center;
      box-shadow: 0 4px 24px rgba(0,0,0,0.08);
    }
    .icon { font-size: 48px; margin-bottom: 20px; }
    h1 { font-size: 24px; color: #111; margin-bottom: 12px; }
    p { color: #6b7280; line-height: 1.6; margin-bottom: 10px; font-size: 15px; }
    code { background: #f3f4f6; padding: 2px 6px; border-radius: 4px; font-size: 13px; }
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">✗</div>
    <h1>Connection failed</h1>
    <p>Something went wrong connecting your device to PaceTrace.</p>
    <p>Please close this tab and try again:<br>
       <code>/terra/connect?user=yourname</code></p>
  </div>
</body>
</html>""")
