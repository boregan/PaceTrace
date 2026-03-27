"""
intervals.icu onboarding — full setup flow for PaceTrace v2.

Pages:
  GET  /v2/connect  — setup guide + API key form
  POST /v2/connect  — validate key, save user, show success + upload
  POST /v2/upload   — accept Strava export ZIP, upload activities to intervals.icu
"""

import asyncio
import io
import json
import zipfile
from pathlib import Path

from fastapi import APIRouter, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
import httpx

from ..db.users import upsert_user, get_user

router = APIRouter()

SUPPORTED_EXTENSIONS = {".fit", ".gpx", ".tcx", ".fit.gz", ".gpx.gz", ".tcx.gz"}


def _is_activity_file(name: str) -> bool:
    lower = name.lower()
    return any(lower.endswith(ext) for ext in SUPPORTED_EXTENSIONS)


# ── Page 1: Setup guide ──────────────────────────────────────────────────────

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
        label { display: block; font-size: 13px; color: #999; margin-bottom: 6px; }
        input[type=text], input[type=password] { width: 100%; padding: 12px; background: #111; border: 1px solid #333; border-radius: 8px; color: #fff; font-size: 14px; font-family: monospace; }
        input:focus { outline: none; border-color: #ff4500; }
        .fields { display: flex; flex-direction: column; gap: 16px; margin: 16px 0; }
        button { width: 100%; padding: 14px; background: #ff4500; color: #fff; border: none; border-radius: 8px; font-size: 16px; font-weight: 600; cursor: pointer; }
        button:hover { background: #e03d00; }
        .note { background: #111; border: 1px solid #333; border-radius: 8px; padding: 14px; font-size: 13px; color: #888; line-height: 1.6; margin-top: 12px; }
        .error { background: #2a1010; border: 1px solid #ff3333; color: #ff6666; }
    </style>
</head>
<body>
<div class="container">
    <h1>PaceTrace</h1>
    <p class="subtitle">Get set up in about 5 minutes. You'll need a free intervals.icu account.</p>

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

    <div class="phase">
        <div class="phase-header">
            <span class="phase-num">2</span>
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


# ── Page 2: Success + history upload ──────────────────────────────────────────

SUCCESS_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>PaceTrace — Connected!</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: -apple-system, system-ui, sans-serif; background: #0a0a0a; color: #e0e0e0; min-height: 100vh; padding: 40px 20px; }}
        .container {{ max-width: 600px; margin: 0 auto; }}
        h1 {{ font-size: 28px; margin-bottom: 4px; color: #fff; }}
        .success {{ color: #4caf50; font-size: 15px; margin-bottom: 32px; }}
        .phase {{ background: #1a1a1a; border: 1px solid #333; border-radius: 16px; padding: 32px; margin-bottom: 24px; }}
        .phase-header {{ display: flex; align-items: center; gap: 12px; margin-bottom: 20px; }}
        .phase-num {{ background: #4caf50; color: #fff; width: 28px; height: 28px; border-radius: 50%; text-align: center; line-height: 28px; font-size: 16px; flex-shrink: 0; }}
        .phase-title {{ font-size: 18px; font-weight: 600; color: #fff; }}
        .phase-desc {{ color: #999; font-size: 14px; line-height: 1.7; margin-bottom: 16px; }}
        .phase-desc a {{ color: #ff4500; text-decoration: none; }}
        .substep {{ padding: 8px 0 8px 16px; border-left: 2px solid #333; margin: 8px 0; color: #ccc; font-size: 14px; line-height: 1.6; }}
        .substep a {{ color: #ff4500; text-decoration: none; }}
        .config {{ background: #111; border: 1px solid #333; border-radius: 8px; padding: 16px; margin: 12px 0; font-family: monospace; font-size: 12px; color: #ccc; white-space: pre-wrap; word-break: break-all; cursor: pointer; position: relative; }}
        .config:hover {{ border-color: #ff4500; }}
        .config::after {{ content: 'click to copy'; position: absolute; right: 8px; top: 8px; font-size: 10px; color: #666; font-family: -apple-system, system-ui, sans-serif; }}
        .section {{ margin-bottom: 24px; }}
        .section h3 {{ font-size: 14px; color: #999; margin-bottom: 8px; }}
        .note {{ background: #111; border: 1px solid #333; border-radius: 8px; padding: 14px; font-size: 13px; color: #888; line-height: 1.6; margin-top: 12px; }}
        .optional {{ display: inline-block; background: #2a2a1a; color: #ff9800; font-size: 11px; padding: 2px 8px; border-radius: 4px; margin-left: 8px; }}
        .add-btn {{ display: inline-flex; align-items: center; gap: 8px; background: #ff4500; color: #fff; border: none; border-radius: 8px; padding: 12px 20px; font-size: 14px; font-weight: 600; cursor: pointer; text-decoration: none; transition: background 0.2s; margin: 8px 0; }}
        .add-btn:hover {{ background: #cc3700; }}
        .add-btn svg {{ flex-shrink: 0; }}

        /* Drop zone */
        .dropzone {{ border: 2px dashed #333; border-radius: 12px; padding: 40px 20px; text-align: center; cursor: pointer; transition: all 0.2s; margin: 16px 0; }}
        .dropzone:hover, .dropzone.dragover {{ border-color: #ff4500; background: #1a1010; }}
        .dropzone-text {{ color: #888; font-size: 14px; }}
        .dropzone-text b {{ color: #ff4500; }}
        .dropzone input {{ display: none; }}

        /* Progress */
        #upload-progress {{ display: none; margin-top: 16px; }}
        .progress-bar {{ background: #111; border-radius: 8px; height: 8px; overflow: hidden; }}
        .progress-fill {{ background: #ff4500; height: 100%; width: 0%; transition: width 0.3s; border-radius: 8px; }}
        .progress-text {{ font-size: 13px; color: #999; margin-top: 8px; }}
        .progress-log {{ background: #111; border: 1px solid #333; border-radius: 8px; padding: 12px; margin-top: 12px; font-family: monospace; font-size: 11px; color: #888; max-height: 200px; overflow-y: auto; white-space: pre-wrap; }}
    </style>
</head>
<body>
<div class="container">
    <h1>You're in.</h1>
    <p class="success">Welcome {display_name} — your intervals.icu account is linked.</p>

    <!-- Claude connection -->
    <div class="phase">
        <div class="phase-header">
            <span class="phase-num">&check;</span>
            <span class="phase-title">Add to Claude</span>
        </div>
        <div class="section">
            <a class="add-btn" href="https://claude.ai/settings/integrations/new?url={base_url}/v2/mcp/sse%3Fuser%3D{username}&name=PaceTrace" target="_blank">
                <svg width="18" height="18" viewBox="0 0 18 18" fill="none"><circle cx="9" cy="9" r="8.5" stroke="white" stroke-opacity="0.4"/><path d="M9 5v4l2.5 2.5" stroke="white" stroke-width="1.5" stroke-linecap="round"/></svg>
                Add PaceTrace to Claude
            </a>
            <div class="note" style="margin-top:12px">
                Or paste this URL manually at <a href="https://claude.ai/settings/integrations" target="_blank">claude.ai → Settings → Integrations</a>:<br><br>
                <div class="config" onclick="navigator.clipboard.writeText(this.dataset.url); this.style.borderColor='#4caf50'; setTimeout(()=>this.style.borderColor='',1500)" data-url="{base_url}/v2/mcp/sse?user={username}">{base_url}/v2/mcp/sse?user={username}</div>
            </div>
        </div>
    </div>

    <!-- History import -->
    <div class="phase">
        <div class="phase-header">
            <span class="phase-num">+</span>
            <span class="phase-title">Import your run history</span>
            <span class="optional">optional</span>
        </div>
        <div class="phase-desc">
            intervals.icu free tier only syncs going forward. To get your full history
            (fitness trends, pace progression, etc), export from <b>Garmin Connect</b> or <b>Strava</b>
            and drop the ZIP below.
        </div>

        <div class="substep">
            <b>Garmin Connect (recommended)</b> — gives full FIT files with all sensor data
            (1-sec HR, cadence, stride, power, temperature).<br>
            Go to <a href="https://www.garmin.com/en-US/account/datamanagement/exportdata/" target="_blank">garmin.com → Account → Data Management → Export Your Data</a>.
            Request the export — Garmin emails you a ZIP.
        </div>
        <div class="substep">
            <b>Strava</b> — gives GPX files, good enough for pace/HR/distance but less detail.<br>
            Go to <a href="https://www.strava.com/athlete/delete_your_account" target="_blank">Strava → Settings → "Download or Delete Your Account"</a> → "Request Your Archive".
        </div>
        <div class="substep">
            Drop whichever ZIP you get below — we handle both formats.
        </div>

        <div class="dropzone" id="dropzone">
            <div class="dropzone-text">
                <b>Drop your Garmin or Strava export ZIP here</b><br>
                or click to select
            </div>
            <input type="file" id="file-input" accept=".zip">
        </div>

        <div id="upload-progress">
            <div class="progress-bar"><div class="progress-fill" id="progress-fill"></div></div>
            <div class="progress-text" id="progress-text">Starting...</div>
            <div class="progress-log" id="progress-log"></div>
        </div>

        <div class="note">
            Don't worry if you skip this — PaceTrace works without history.
            Your data builds over time as you run.
        </div>
    </div>
</div>

<script>
const USERNAME = "{username}";
const dropzone = document.getElementById('dropzone');
const fileInput = document.getElementById('file-input');
const progress = document.getElementById('upload-progress');
const progressFill = document.getElementById('progress-fill');
const progressText = document.getElementById('progress-text');
const progressLog = document.getElementById('progress-log');

dropzone.addEventListener('click', () => fileInput.click());
dropzone.addEventListener('dragover', e => {{ e.preventDefault(); dropzone.classList.add('dragover'); }});
dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
dropzone.addEventListener('drop', e => {{
    e.preventDefault();
    dropzone.classList.remove('dragover');
    if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
}});
fileInput.addEventListener('change', () => {{
    if (fileInput.files.length) handleFile(fileInput.files[0]);
}});

async function handleFile(file) {{
    if (!file.name.endsWith('.zip')) {{
        alert('Please select a ZIP file');
        return;
    }}

    dropzone.style.display = 'none';
    progress.style.display = 'block';
    progressText.textContent = `Reading ${{file.name}} (${{(file.size / 1024 / 1024).toFixed(1)}} MB)...`;
    progressLog.textContent = '';

    const formData = new FormData();
    formData.append('file', file);
    formData.append('username', USERNAME);

    try {{
        const resp = await fetch('/v2/upload', {{ method: 'POST', body: formData }});
        if (!resp.ok || !resp.body) {{
            const err = await resp.json().catch(() => ({{error: 'Upload failed'}}));
            throw new Error(err.error || `HTTP ${{resp.status}}`);
        }}

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buf = '';

        while (true) {{
            const {{ done, value }} = await reader.read();
            if (done) break;
            buf += decoder.decode(value, {{ stream: true }});

            // Parse complete SSE events from buffer
            const lines = buf.split('\\n\\n');
            buf = lines.pop(); // Keep incomplete last chunk

            for (const chunk of lines) {{
                const dataLine = chunk.split('\\n').find(l => l.startsWith('data: '));
                if (!dataLine) continue;
                let evt;
                try {{ evt = JSON.parse(dataLine.slice(6)); }} catch {{ continue; }}

                if (evt.type === 'start') {{
                    progressText.textContent = `Uploading ${{evt.total}} activities...`;

                }} else if (evt.type === 'progress') {{
                    const pct = Math.round((evt.idx / evt.total) * 100);
                    progressFill.style.width = pct + '%';
                    progressText.textContent = `${{evt.idx}}/${{evt.total}} — ${{evt.uploaded}} uploaded, ${{evt.skipped}} already existed, ${{evt.failed}} failed`;
                    const icon = evt.status === 'uploaded' ? '✓' : evt.status === 'exists' ? '—' : '✗';
                    progressLog.textContent += `${{icon}} ${{evt.file}} (${{evt.status}})\n`;
                    progressLog.scrollTop = progressLog.scrollHeight;

                }} else if (evt.type === 'done') {{
                    progressFill.style.width = '100%';
                    progressText.textContent = `Done! ${{evt.uploaded}} uploaded, ${{evt.skipped}} already existed, ${{evt.failed}} failed`;
                }}
            }}
        }}
    }} catch (e) {{
        progressText.textContent = `Upload failed: ${{e.message}}`;
        progressFill.style.background = '#ff3333';
        progressFill.style.width = '100%';
    }}
}}
</script>
</body>
</html>
"""


# ── Routes ────────────────────────────────────────────────────────────────────

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
    except httpx.HTTPStatusError:
        return HTMLResponse(
            CONNECT_HTML.replace(
                "</form>",
                '<div class="note error">Invalid API key or athlete ID. Please check and try again.</div></form>',
            ),
            status_code=400,
        )
    except Exception as e:
        return HTMLResponse(
            CONNECT_HTML.replace(
                "</form>",
                f'<div class="note error">Connection error: {e}</div></form>',
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
    upsert_user(
        username=slug,
        display_name=name or display_name,
        icu_athlete_id=real_athlete_id,
        icu_api_key=api_key.strip(),
    )

    # Build success page
    base_url = str(request.base_url).rstrip("/")

    html = SUCCESS_HTML.format(
        display_name=name or display_name,
        username=slug,
        base_url=base_url,
    )
    return HTMLResponse(html)


def _sse(data: dict) -> str:
    """Format a dict as an SSE event line."""
    return f"data: {json.dumps(data)}\n\n"


@router.post("/v2/upload")
async def upload_history_export(
    username: str = Form(...),
    file: UploadFile = File(...),
):
    """Accept a Garmin or Strava export ZIP, stream SSE progress back as it uploads."""

    user = get_user(username)
    if not user or not user.get("icu_api_key"):
        return JSONResponse(
            {"success": False, "error": "User not found or not connected to intervals.icu"},
            status_code=400,
        )

    api_key = user["icu_api_key"]

    # Read ZIP into memory (limit 500MB)
    try:
        content = await file.read()
        if len(content) > 500 * 1024 * 1024:
            return JSONResponse(
                {"success": False, "error": "File too large (max 500MB). Try splitting it."},
                status_code=400,
            )
        zf = zipfile.ZipFile(io.BytesIO(content))
    except Exception as e:
        return JSONResponse(
            {"success": False, "error": f"Invalid ZIP file: {e}"},
            status_code=400,
        )

    # Collect activity files (Garmin nested dirs + nested ZIPs)
    activity_files = sorted([n for n in zf.namelist() if _is_activity_file(n)])
    nested_zip_data: list[tuple[str, bytes]] = []
    for name in zf.namelist():
        if name.lower().endswith(".zip"):
            try:
                inner_bytes = zf.read(name)
                inner_zf = zipfile.ZipFile(io.BytesIO(inner_bytes))
                for inner_name in inner_zf.namelist():
                    if _is_activity_file(inner_name):
                        nested_zip_data.append((Path(inner_name).name, inner_zf.read(inner_name)))
                inner_zf.close()
            except Exception:
                pass

    if not activity_files and not nested_zip_data:
        return JSONResponse(
            {"success": False, "error": "No activity files (FIT/GPX/TCX) found in the ZIP"},
            status_code=400,
        )

    total = len(activity_files) + len(nested_zip_data)

    async def _stream():
        yield _sse({"type": "start", "total": total})

        uploaded = skipped = failed = idx = 0

        async with httpx.AsyncClient(
            base_url="https://intervals.icu/api/v1",
            auth=httpx.BasicAuth("API_KEY", api_key),
            timeout=30.0,
        ) as client:

            async def _upload(fname: str, file_bytes: bytes):
                nonlocal uploaded, skipped, failed, idx
                idx += 1
                try:
                    resp = await client.post(
                        "/athlete/0/activities",
                        files={"file": (fname, file_bytes)},
                    )
                    if resp.status_code in (200, 201):
                        uploaded += 1
                        status = "uploaded"
                    elif resp.status_code == 409:
                        skipped += 1
                        status = "exists"
                    else:
                        failed += 1
                        status = f"error {resp.status_code}"
                except Exception as e:
                    failed += 1
                    status = str(e)[:80]

                return status

            for name in activity_files:
                fname = Path(name).name
                status = await _upload(fname, zf.read(name))
                yield _sse({"type": "progress", "idx": idx, "total": total,
                            "file": fname, "status": status,
                            "uploaded": uploaded, "skipped": skipped, "failed": failed})
                await asyncio.sleep(0.3)

            for fname, file_bytes in nested_zip_data:
                status = await _upload(fname, file_bytes)
                yield _sse({"type": "progress", "idx": idx, "total": total,
                            "file": fname, "status": status,
                            "uploaded": uploaded, "skipped": skipped, "failed": failed})
                await asyncio.sleep(0.3)

        yield _sse({"type": "done", "uploaded": uploaded, "skipped": skipped,
                    "failed": failed, "total": total})

    return StreamingResponse(_stream(), media_type="text/event-stream")
