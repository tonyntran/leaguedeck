from fastapi import FastAPI

app = FastAPI(title="LeagueDeck")


@app.get("/health")
def health():
    return {"status": "ok"}
