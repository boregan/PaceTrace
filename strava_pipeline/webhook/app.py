"""
PaceTrace FastAPI application.

Routes:
  /webhook            — Strava webhook (challenge + event handler)
  /webhook/terra      — Terra health data webhook (sleep, daily, auth events)
  /terra/connect      — Connect a wearable via Terra OAuth widget
  /terra/success      — Terra OAuth success callback
  /terra/failure      — Terra OAuth failure callback
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
from fastapi.middleware.cors import CORSMiddleware

from strava_pipeline.webhook.handlers import router as webhook_router
from strava_pipeline.webhook.terra_handler import router as terra_webhook_router
from strava_pipeline.api.routes import router as api_router
from strava_pipeline.web.onboarding import router as onboarding_router
from strava_pipeline.web.terra import router as terra_router

load_dotenv()

app = FastAPI(title="PaceTrace")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)
app.include_router(webhook_router)
app.include_router(terra_webhook_router)
app.include_router(api_router)
app.include_router(onboarding_router)
app.include_router(terra_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
