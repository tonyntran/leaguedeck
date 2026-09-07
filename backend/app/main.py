from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db import init_db
from app.routers import auth as auth_router
from app.routers import leagues as leagues_router
from app.routers import settings as settings_router
from app.sync import sync_all_platforms

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
        scheduler.start()
        sync_all_platforms()
    yield
    if scheduler.running:
        scheduler.shutdown()


app = FastAPI(title="LeagueDeck", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router.router)
app.include_router(settings_router.router)
app.include_router(leagues_router.router)
app.include_router(leagues_router.sync_status_router)


@app.get("/health")
def health():
    return {"status": "ok"}
