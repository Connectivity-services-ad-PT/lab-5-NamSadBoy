import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from pydantic import BaseModel, Field


SERVICE_VERSION = os.getenv("SERVICE_VERSION", "1.0.0")

app = FastAPI(
    title="Smart Campus Core Audit Service",
    version=SERVICE_VERSION,
    description="Internal event sink representing Analytics/Notification integration.",
)


class AuditEvent(BaseModel):
    eventType: str = Field(min_length=3, max_length=100)
    payload: dict[str, Any]


events: list[dict[str, Any]] = []


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "core-audit-service",
        "version": SERVICE_VERSION,
    }


@app.post("/events", status_code=202)
def ingest_event(event: AuditEvent) -> dict[str, str]:
    event_id = str(uuid4())
    events.append(
        {
            "eventId": event_id,
            "eventType": event.eventType,
            "payload": event.payload,
            "receivedAt": datetime.now(timezone.utc).isoformat(),
        }
    )
    return {"eventId": event_id, "status": "accepted"}


@app.get("/events")
def list_events() -> dict[str, Any]:
    return {"items": events[-100:], "count": len(events[-100:])}
