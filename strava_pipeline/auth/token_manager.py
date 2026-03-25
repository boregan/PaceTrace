from __future__ import annotations

"""
Manages Strava OAuth2 tokens for a single user.

Loads tokens from a per-user .env file, refreshes when expired,
writes updated tokens back, and returns a ready-to-use stravalib Client.
"""

import os
import time
from pathlib import Path

from dotenv import dotenv_values, set_key
from stravalib.client import Client


REFRESH_BUFFER_SECONDS = 300  # refresh 5 minutes before expiry


def get_client(user_env_path: str | Path) -> Client:
    """Return an authenticated stravalib Client for the given user env file."""
    user_env_path = Path(user_env_path)
    config = dotenv_values(user_env_path)

    client_id = os.environ["STRAVA_CLIENT_ID"]
    client_secret = os.environ["STRAVA_CLIENT_SECRET"]
    access_token = config["STRAVA_ACCESS_TOKEN"]
    refresh_token = config["STRAVA_REFRESH_TOKEN"]
    expires_at = int(config["STRAVA_TOKEN_EXPIRES_AT"])

    if time.time() >= expires_at - REFRESH_BUFFER_SECONDS:
        access_token, refresh_token, expires_at = _refresh(
            client_id, client_secret, refresh_token, user_env_path
        )

    client = Client()
    client.access_token = access_token
    client.refresh_token = refresh_token
    client.token_expires = expires_at
    return client


def get_client_from_db(athlete_id: int) -> Client:
    """Return an authenticated stravalib Client for a DB-stored user."""
    from strava_pipeline.db.tokens import get_tokens_by_athlete_id, update_tokens

    tokens = get_tokens_by_athlete_id(athlete_id)
    if not tokens:
        raise ValueError(f"No tokens found for athlete_id={athlete_id}")

    client_id = os.environ["STRAVA_CLIENT_ID"]
    client_secret = os.environ["STRAVA_CLIENT_SECRET"]
    access_token = tokens["access_token"]
    refresh_token = tokens["refresh_token"]
    expires_at = int(tokens["token_expires_at"])

    if time.time() >= expires_at - REFRESH_BUFFER_SECONDS:
        client = Client()
        token_response = client.refresh_access_token(
            client_id=int(client_id),
            client_secret=client_secret,
            refresh_token=refresh_token,
        )
        access_token = token_response["access_token"]
        refresh_token = token_response["refresh_token"]
        expires_at = int(token_response["expires_at"])
        update_tokens(athlete_id, access_token, refresh_token, expires_at)
        print(f"[token_manager] Token refreshed for athlete_id={athlete_id}")

    client = Client()
    client.access_token = access_token
    client.refresh_token = refresh_token
    client.token_expires = expires_at
    return client


def _refresh(
    client_id: str,
    client_secret: str,
    refresh_token: str,
    user_env_path: Path,
) -> tuple[str, str, int]:
    """Refresh the access token and persist the new values."""
    client = Client()
    token_response = client.refresh_access_token(
        client_id=int(client_id),
        client_secret=client_secret,
        refresh_token=refresh_token,
    )

    new_access = token_response["access_token"]
    new_refresh = token_response["refresh_token"]
    new_expires = int(token_response["expires_at"])

    set_key(str(user_env_path), "STRAVA_ACCESS_TOKEN", new_access)
    set_key(str(user_env_path), "STRAVA_REFRESH_TOKEN", new_refresh)
    set_key(str(user_env_path), "STRAVA_TOKEN_EXPIRES_AT", str(new_expires))

    print(f"[token_manager] Token refreshed for {user_env_path.stem}")
    return new_access, new_refresh, new_expires
