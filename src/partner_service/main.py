import os
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Response
from pydantic import BaseModel, Field


SERVICE_VERSION = os.getenv("SERVICE_VERSION", "1.0.0")

app = FastAPI(
    title="Smart Campus Partner Service Mock",
    version=SERVICE_VERSION,
    description="Local Notification and Analytics provider used for Buoi 6 readiness tests.",
)


class PartnerPayload(BaseModel):
    model_config = {"extra": "allow"}


class TestMode(BaseModel):
    notificationStatus: int = Field(default=202, ge=100, le=599)
    analyticsStatus: int = Field(default=202, ge=100, le=599)
    delaySeconds: float = Field(default=0, ge=0, le=10)


mode = TestMode()
notifications: list[dict[str, Any]] = []
analytics_events: list[dict[str, Any]] = []


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def maybe_delay() -> None:
    if mode.delaySeconds:
        time.sleep(mode.delaySeconds)


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "partner-service-mock",
        "version": SERVICE_VERSION,
    }


@app.post("/api/v1/notifications", status_code=202)
def create_notification(payload: PartnerPayload, response: Response) -> dict[str, str]:
    maybe_delay()
    response.status_code = mode.notificationStatus
    if mode.notificationStatus >= 400:
        return {"status": "rejected", "provider": "notification"}

    notification_id = str(uuid4())
    notifications.append(
        {
            "notificationId": notification_id,
            "payload": payload.model_dump(),
            "receivedAt": now_iso(),
        }
    )
    return {"notificationId": notification_id, "status": "accepted"}


@app.post("/api/v1/events", status_code=202)
def create_analytics_event(payload: PartnerPayload, response: Response) -> dict[str, str]:
    maybe_delay()
    response.status_code = mode.analyticsStatus
    if mode.analyticsStatus >= 400:
        return {"status": "rejected", "provider": "analytics"}

    event_id = str(uuid4())
    analytics_events.append(
        {
            "eventId": event_id,
            "payload": payload.model_dump(),
            "receivedAt": now_iso(),
        }
    )
    return {"eventId": event_id, "status": "accepted"}


@app.get("/test/received")
def received() -> dict[str, Any]:
    return {
        "notifications": notifications[-100:],
        "analyticsEvents": analytics_events[-100:],
        "notificationCount": len(notifications),
        "analyticsCount": len(analytics_events),
    }


@app.put("/test/mode")
def set_mode(new_mode: TestMode) -> dict[str, Any]:
    global mode
    mode = new_mode
    return mode.model_dump()


@app.delete("/test/reset")
def reset() -> dict[str, str]:
    global mode
    notifications.clear()
    analytics_events.clear()
    mode = TestMode()
    return {"status": "reset"}
