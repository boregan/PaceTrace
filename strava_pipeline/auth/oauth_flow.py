"""
One-time interactive OAuth2 flow for a new user.

Usage:
    python scripts/backfill.py auth --user ben

Generates config/users/<name>.env with the initial token set.
Only needs to run once per athlete.
"""

import os
import webbrowser
from pathlib import Path

from dotenv import set_key
from stravalib.client import Client


REDIRECT_URI = "http://localhost:8080/callback"
SCOPE = "activity:read_all"


def run_oauth_flow(user_name: str) -> Path:
    """
    Walk through OAuth, write tokens to config/users/<user_name>.env.
    Returns the path to the created env file.
    """
    client_id = os.environ["STRAVA_CLIENT_ID"]
    client_secret = os.environ["STRAVA_CLIENT_SECRET"]

    client = Client()
    auth_url = client.authorization_url(
        client_id=int(client_id),
        redirect_uri=REDIRECT_URI,
        scope=[SCOPE],
    )

    print(f"\nOpening browser for Strava authorisation...")
    print(f"If the browser doesn't open, visit:\n  {auth_url}\n")
    webbrowser.open(auth_url)

    redirect_response = input(
        "After approving, paste the full redirect URL here:\n> "
    ).strip()

    # Extract the code from the redirect URL
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(redirect_response)
    code = parse_qs(parsed.query).get("code", [None])[0]
    if not code:
        raise ValueError("No 'code' parameter found in the redirect URL.")

    token_response = client.exchange_code_for_token(
        client_id=int(client_id),
        client_secret=client_secret,
        code=code,
    )

    athlete = client.get_athlete()
    athlete_id = str(athlete.id)

    env_path = Path("config/users") / f"{user_name}.env"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.touch()

    set_key(str(env_path), "STRAVA_ATHLETE_ID", athlete_id)
    set_key(str(env_path), "STRAVA_ACCESS_TOKEN", token_response["access_token"])
    set_key(str(env_path), "STRAVA_REFRESH_TOKEN", token_response["refresh_token"])
    set_key(str(env_path), "STRAVA_TOKEN_EXPIRES_AT", str(int(token_response["expires_at"])))

    print(f"\nTokens saved to {env_path}")
    print(f"Athlete ID: {athlete_id} ({athlete.firstname} {athlete.lastname})")
    return env_path
