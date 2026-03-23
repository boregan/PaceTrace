from __future__ import annotations

import os
from fastapi import Header, HTTPException


async def verify_token(authorization: str = Header(None)) -> None:
    expected = os.environ.get("API_SECRET", "")
    if not expected:
        return  # no secret configured — allow all (dev mode)
    if not authorization or authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="Unauthorized")
