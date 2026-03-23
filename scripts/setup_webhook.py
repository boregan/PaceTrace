#!/usr/bin/env python3
"""
One-time script to register the Strava webhook subscription.

Run this after deploying the webhook server for the first time.

Usage:
    WEBHOOK_URL=https://your-app.railway.app python scripts/setup_webhook.py

Requirements:
    - STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET in .env
    - STRAVA_WEBHOOK_VERIFY_TOKEN in .env
    - WEBHOOK_URL env var pointing to your deployed server
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
from dotenv import load_dotenv

load_dotenv()


def create_subscription(callback_url: str) -> dict:
    client_id = os.environ["STRAVA_CLIENT_ID"]
    client_secret = os.environ["STRAVA_CLIENT_SECRET"]
    verify_token = os.environ["STRAVA_WEBHOOK_VERIFY_TOKEN"]

    response = httpx.post(
        "https://www.strava.com/api/v3/push_subscriptions",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "callback_url": callback_url,
            "verify_token": verify_token,
        },
    )
    response.raise_for_status()
    return response.json()


def list_subscriptions() -> list:
    client_id = os.environ["STRAVA_CLIENT_ID"]
    client_secret = os.environ["STRAVA_CLIENT_SECRET"]
    response = httpx.get(
        "https://www.strava.com/api/v3/push_subscriptions",
        params={"client_id": client_id, "client_secret": client_secret},
    )
    response.raise_for_status()
    return response.json()


def delete_subscription(subscription_id: int) -> None:
    client_id = os.environ["STRAVA_CLIENT_ID"]
    client_secret = os.environ["STRAVA_CLIENT_SECRET"]
    httpx.delete(
        f"https://www.strava.com/api/v3/push_subscriptions/{subscription_id}",
        params={"client_id": client_id, "client_secret": client_secret},
    ).raise_for_status()


if __name__ == "__main__":
    webhook_url = os.environ.get("WEBHOOK_URL")
    if not webhook_url:
        print("ERROR: Set WEBHOOK_URL environment variable to your deployed webhook URL.")
        print("Example: WEBHOOK_URL=https://your-app.railway.app python scripts/setup_webhook.py")
        sys.exit(1)

    callback_url = webhook_url.rstrip("/") + "/webhook"

    print(f"Checking existing subscriptions...")
    existing = list_subscriptions()
    if existing:
        print(f"Found {len(existing)} existing subscription(s):")
        for sub in existing:
            print(f"  ID={sub['id']} callback={sub['callback_url']}")
        if input("Delete existing and create new? [y/N] ").lower() != "y":
            sys.exit(0)
        for sub in existing:
            delete_subscription(sub["id"])
            print(f"  Deleted subscription {sub['id']}")

    print(f"\nCreating subscription for: {callback_url}")
    result = create_subscription(callback_url)
    print(f"Success! Subscription ID: {result.get('id')}")
    print("Strava will now POST new activity events to your webhook.")
