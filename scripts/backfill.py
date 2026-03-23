#!/usr/bin/env python3
"""
CLI entry point for the Strava backfill pipeline.

Usage:
    # First-time OAuth setup for a new user:
    python scripts/backfill.py auth --user ben

    # Run full backfill for all users:
    python scripts/backfill.py run

    # Run for a specific user only:
    python scripts/backfill.py run --user ben

    # Only sync activities after a specific date (Unix timestamp):
    python scripts/backfill.py run --user ben --after 1700000000

    # Only fill missing stream data (skip activity fetch):
    python scripts/backfill.py run --user ben --streams-only

    # Preview without writing to database:
    python scripts/backfill.py run --user ben --dry-run

    # Show current rate limit status:
    python scripts/backfill.py status
"""

import sys
from pathlib import Path

# Allow running from project root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))

import click
from dotenv import load_dotenv

load_dotenv()


@click.group()
def cli():
    """Strava → Supabase pipeline tools."""
    pass


@cli.command()
@click.option("--user", required=True, help="Name for this user (e.g. ben)")
def auth(user: str):
    """Run OAuth flow to get tokens for a new user."""
    from strava_pipeline.auth.oauth_flow import run_oauth_flow
    run_oauth_flow(user)


@cli.command()
@click.option("--user", default=None, help="Specific user name (default: all users)")
@click.option("--after", default=None, type=int, help="Only fetch activities after this Unix timestamp")
@click.option("--streams-only", is_flag=True, help="Skip activity fetch, only fill missing streams")
@click.option("--dry-run", is_flag=True, help="Print actions without writing to database")
def run(user, after, streams_only, dry_run):
    """Run the backfill pipeline."""
    from strava_pipeline.backfill.runner import run as _run
    _run(user_name=user, after=after, streams_only=streams_only, dry_run=dry_run)


@cli.command()
def status():
    """Show current Strava rate limit usage."""
    from strava_pipeline.utils.rate_limiter import remaining
    r = remaining()
    click.echo(f"15-min window: {r['15min_used']} used / {r['15min_remaining']} remaining")
    click.echo(f"Daily window:  {r['day_used']} used / {r['day_remaining']} remaining")


@cli.command()
@click.argument("activity_id", type=int)
@click.option("--max-points", default=120, show_default=True, help="Max stream samples to show")
@click.option("--max-hr", default=185, show_default=True, help="Athlete max HR for zone calculation")
def summary(activity_id: int, max_points: int, max_hr: int):
    """Print a Claude-ready summary of an activity."""
    from strava_pipeline.claude.query_helper import build_context
    print(build_context(activity_id, max_points=max_points, max_hr=max_hr))


if __name__ == "__main__":
    cli()
