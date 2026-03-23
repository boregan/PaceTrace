from __future__ import annotations

"""
Discovers and loads per-user config files from config/users/*.env
"""

from pathlib import Path

from dotenv import dotenv_values


USERS_DIR = Path(__file__).parent.parent.parent / "config" / "users"


def get_all_users() -> list[dict]:
    """Return a list of user config dicts for every *.env file in config/users/."""
    users = []
    for env_file in sorted(USERS_DIR.glob("*.env")):
        if env_file.name.endswith(".env.example"):
            continue
        config = dotenv_values(env_file)
        config["_env_path"] = str(env_file)
        config["_user_name"] = env_file.stem
        users.append(config)
    return users


def get_user_by_athlete_id(athlete_id: int | str) -> dict | None:
    """Return the config dict for the user with the given athlete ID, or None."""
    athlete_id = str(athlete_id)
    for user in get_all_users():
        if user.get("STRAVA_ATHLETE_ID") == athlete_id:
            return user
    return None


def get_user_by_name(name: str) -> dict | None:
    """Return the config dict for the user with the given file stem name."""
    env_file = USERS_DIR / f"{name}.env"
    if not env_file.exists():
        return None
    config = dotenv_values(env_file)
    config["_env_path"] = str(env_file)
    config["_user_name"] = name
    return config
