from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI

from app.config import get_settings
from app.db import init_db
from app.routers import auth as auth_router
from app.routers import leagues as leagues_router
from app.routers import live_scores as live_scores_router
from app.routers import settings as settings_router
from app.sync import sync_all_platforms, sync_all_platforms_during_live_window

scheduler = BackgroundScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if get_settings().enable_scheduler:
        scheduler.add_job(
            sync_all_platforms,
            "interval",
            minutes=20,
            id="sync_all_platforms",
            replace_existing=True,
        )
        # A second, independent job -- outside a likely-live game window
        # this is a no-op (see sync.py), so it adds no load the rest of
        # the time. The 20-minute job above keeps running regardless.
        scheduler.add_job(
            sync_all_platforms_during_live_window,
            "interval",
            seconds=60,
            id="sync_all_platforms_during_live_window",
            replace_existing=True,
        )
        scheduler.start()
        sync_all_platforms()
    yield
    if scheduler.running:
        scheduler.shutdown()


app = FastAPI(title="LeagueDeck", lifespan=lifespan)

# No CORS middleware: the browser is always same-origin with the API. In Docker
# nginx proxies /auth, /settings, /leagues, /sync-status and /health through to
# this app; in local development Vite's server.proxy does the same. Adding CORS
# back would only be needed if the SPA were served from a different origin.

app.include_router(auth_router.router)
app.include_router(settings_router.router)
app.include_router(leagues_router.router)
app.include_router(leagues_router.sync_status_router)
app.include_router(live_scores_router.router)


@app.get("/health")
def health():
    return {"status": "ok"}
