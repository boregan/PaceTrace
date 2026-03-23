"""
PaceTrace FastAPI application.

Routes:
  /webhook            — Strava webhook (challenge + event handler)
  /activity/{id}/summary
  /athlete/{user}/week
  /athlete/{user}/recent
  /athlete/{user}/stats
  /health

Start with:
    uvicorn strava_pipeline.webhook.app:app --host 0.0.0.0 --port $PORT
"""

from dotenv import load_dotenv
from fastapi import FastAPI

from strava_pipeline.webhook.handlers import router as webhook_router
from strava_pipeline.api.routes import router as api_router

load_dotenv()

app = FastAPI(title="PaceTrace")
app.include_router(webhook_router)
app.include_router(api_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
