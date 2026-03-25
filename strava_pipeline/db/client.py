from __future__ import annotations

"""
Supabase client singleton.
Reads SUPABASE_URL and SUPABASE_SERVICE_KEY from the environment.
"""

import os
import httpx
from supabase import create_client, Client
from supabase.lib.client_options import SyncClientOptions

_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_SERVICE_KEY"]
        _client = create_client(
            url, key,
            options=SyncClientOptions(httpx_client=httpx.Client(http2=False))
        )
    return _client
