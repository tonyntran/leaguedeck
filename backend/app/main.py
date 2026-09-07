from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import init_db
from app.routers import auth as auth_router
from app.routers import settings as settings_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="LeagueDeck", lifespan=lifespan)

app.include_router(auth_router.router)
app.include_router(settings_router.router)


@app.get("/health")
def health():
    return {"status": "ok"}
