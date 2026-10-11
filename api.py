"""FastAPI: kleine HTTP-API, damit andere Server die Daten abfragen können.

Endpunkte:
    GET /api/all      – kompletter Schnappschuss (Status, Gerät, Felder, Werte, Settings)
    GET /api/status   – Verbindungsstatus & Geräte-Info
    GET /api/data     – aktuelle Runtime-Werte (key → Wert)
    GET /api/settings – aktuelle Settings
    GET /api/fields   – Feld-Metadaten inkl. aktuellem Wert
    GET /api/history  – Historie (?fields=a,b&minutes=60&max_points=…)
Interaktive Doku: GET /docs
"""

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from datastore import DataStore


def create_app(store: DataStore) -> FastAPI:
    app = FastAPI(
        title="GoodWe Service API",
        description="Daten des GoodWe-Wechselrichters, gesammelt im 60s-Polling-Loop.",
        version="1.0.0",
    )

    # Andere Server sollen die Daten abfragen dürfen
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("/docs")

    @app.get("/api/all")
    async def get_all():
        return store.snapshot()

    @app.get("/api/status")
    async def get_status():
        snap = store.snapshot()
        return {
            "status": snap["status"],
            "device": snap["device"],
            "last_update": snap["last_update"],
        }

    @app.get("/api/data")
    async def get_data():
        return store.snapshot()["runtime"]

    @app.get("/api/settings")
    async def get_settings():
        return store.snapshot()["settings"]

    @app.get("/api/fields")
    async def get_fields():
        return store.snapshot()["fields"]

    @app.get("/api/history")
    async def get_history(
        fields: str | None = Query(default=None, description="Komma-getrennte Feldnamen, leer = alle"),
        minutes: int | None = Query(default=None, description="Zeitfenster in Minuten"),
        max_points: int | None = Query(default=None, description="Maximale Anzahl Messpunkte"),
    ):
        return store.history_series(fields=fields, minutes=minutes, max_points=max_points)

    return app
