from fastapi import FastAPI

from app.routers import auth as auth_router

app = FastAPI(title="LeagueDeck")

app.include_router(auth_router.router)


@app.get("/health")
def health():
    return {"status": "ok"}
