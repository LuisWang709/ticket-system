from fastapi import FastAPI

app = FastAPI()

EVENTS = [{"id": 1, "name": "Fake Band Live", "venue": "Demo Arena"}]


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/events")
def list_events():
    return EVENTS