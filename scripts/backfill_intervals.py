#!/usr/bin/env python3
"""
Backfill intervals.icu with historical Strava activities.

Takes a Strava bulk export ZIP (or directory of GPX/FIT/TCX files)
and uploads them to intervals.icu via their API. This fills the gap
for free-tier intervals.icu users who don't have historical import.

Usage:
    # From a Strava bulk export ZIP:
    python scripts/backfill_intervals.py --zip ~/Downloads/export_12345.zip \
        --api-key YOUR_ICU_API_KEY

    # From a directory of GPX/FIT files:
    python scripts/backfill_intervals.py --dir ~/strava_exports/ \
        --api-key YOUR_ICU_API_KEY

    # Using credentials from PaceTrace DB:
    python scripts/backfill_intervals.py --zip ~/Downloads/export.zip \
        --user ben
"""

import argparse
import os
import sys
import time
import zipfile
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

ICU_API = "https://intervals.icu/api/v1"
SUPPORTED_EXTENSIONS = {".fit", ".gpx", ".tcx", ".fit.gz", ".gpx.gz", ".tcx.gz"}
# Be polite to the API
DELAY_BETWEEN_UPLOADS = 1.0  # seconds


def _is_activity_file(name: str) -> bool:
    """Check if a filename is a supported activity file."""
    lower = name.lower()
    return any(lower.endswith(ext) for ext in SUPPORTED_EXTENSIONS)


def _get_credentials(user: str | None, api_key: str | None) -> tuple[str, str]:
    """Get API key and athlete ID from args or DB."""
    if api_key:
        return api_key, "0"

    if user:
        from strava_pipeline.db.users import get_user
        u = get_user(user)
        if u and u.get("icu_api_key"):
            return u["icu_api_key"], u.get("icu_athlete_id", "0")

    # Fall back to env
    key = os.environ.get("INTERVALS_ICU_API_KEY", "")
    aid = os.environ.get("INTERVALS_ICU_ATHLETE_ID", "0")
    if key:
        return key, aid

    print("ERROR: No API key provided. Use --api-key, --user, or set INTERVALS_ICU_API_KEY")
    sys.exit(1)


def _upload_file(
    client: httpx.Client,
    file_path: Path,
    file_bytes: bytes | None = None,
    filename: str | None = None,
) -> tuple[bool, str]:
    """Upload a single activity file to intervals.icu. Returns (success, message)."""
    fname = filename or file_path.name
    content = file_bytes or file_path.read_bytes()

    try:
        resp = client.post(
            f"/athlete/0/activities",
            files={"file": (fname, content)},
        )
        if resp.status_code in (200, 201):
            data = resp.json() if resp.content else {}
            act_id = data.get("id", "?")
            return True, f"OK → {act_id}"
        elif resp.status_code == 409:
            return True, "already exists (skipped)"
        else:
            return False, f"HTTP {resp.status_code}: {resp.text[:100]}"
    except Exception as e:
        return False, f"error: {e}"


def backfill_from_zip(zip_path: str, api_key: str, athlete_id: str = "0"):
    """Extract and upload all activity files from a Strava export ZIP."""
    zip_path = Path(zip_path)
    if not zip_path.exists():
        print(f"ERROR: ZIP file not found: {zip_path}")
        sys.exit(1)

    print(f"Opening {zip_path.name}...")

    with zipfile.ZipFile(zip_path) as zf:
        activity_files = [n for n in zf.namelist() if _is_activity_file(n)]

    if not activity_files:
        print("No activity files (FIT/GPX/TCX) found in the ZIP.")
        sys.exit(1)

    print(f"Found {len(activity_files)} activity files")
    print(f"Uploading to intervals.icu (athlete {athlete_id})...")
    print()

    success = 0
    skipped = 0
    failed = 0

    with (
        zipfile.ZipFile(zip_path) as zf,
        httpx.Client(
            base_url=ICU_API,
            auth=httpx.BasicAuth("API_KEY", api_key),
            timeout=30.0,
        ) as client,
    ):
        for i, name in enumerate(sorted(activity_files), 1):
            file_bytes = zf.read(name)
            fname = Path(name).name
            ok, msg = _upload_file(client, Path(name), file_bytes, fname)

            status = "✓" if ok else "✗"
            print(f"  [{i}/{len(activity_files)}] {status} {fname} — {msg}")

            if ok and "already exists" in msg:
                skipped += 1
            elif ok:
                success += 1
            else:
                failed += 1

            if i < len(activity_files):
                time.sleep(DELAY_BETWEEN_UPLOADS)

    print()
    print(f"Done! {success} uploaded, {skipped} skipped, {failed} failed")


def backfill_from_dir(dir_path: str, api_key: str, athlete_id: str = "0"):
    """Upload all activity files from a directory."""
    dir_path = Path(dir_path)
    if not dir_path.is_dir():
        print(f"ERROR: Directory not found: {dir_path}")
        sys.exit(1)

    activity_files = sorted([
        f for f in dir_path.rglob("*")
        if f.is_file() and _is_activity_file(f.name)
    ])

    if not activity_files:
        print("No activity files (FIT/GPX/TCX) found in the directory.")
        sys.exit(1)

    print(f"Found {len(activity_files)} activity files")
    print(f"Uploading to intervals.icu (athlete {athlete_id})...")
    print()

    success = 0
    skipped = 0
    failed = 0

    with httpx.Client(
        base_url=ICU_API,
        auth=httpx.BasicAuth("API_KEY", api_key),
        timeout=30.0,
    ) as client:
        for i, fpath in enumerate(activity_files, 1):
            ok, msg = _upload_file(client, fpath)

            status = "✓" if ok else "✗"
            print(f"  [{i}/{len(activity_files)}] {status} {fpath.name} — {msg}")

            if ok and "already exists" in msg:
                skipped += 1
            elif ok:
                success += 1
            else:
                failed += 1

            if i < len(activity_files):
                time.sleep(DELAY_BETWEEN_UPLOADS)

    print()
    print(f"Done! {success} uploaded, {skipped} skipped, {failed} failed")


def main():
    parser = argparse.ArgumentParser(
        description="Backfill intervals.icu with historical Strava activities"
    )
    parser.add_argument("--zip", help="Path to Strava bulk export ZIP file")
    parser.add_argument("--dir", help="Path to directory of activity files")
    parser.add_argument("--api-key", help="intervals.icu API key")
    parser.add_argument("--user", help="PaceTrace username (to look up API key from DB)")
    args = parser.parse_args()

    if not args.zip and not args.dir:
        parser.error("Provide either --zip or --dir")

    api_key, athlete_id = _get_credentials(args.user, args.api_key)

    if args.zip:
        backfill_from_zip(args.zip, api_key, athlete_id)
    else:
        backfill_from_dir(args.dir, api_key, athlete_id)


if __name__ == "__main__":
    main()
