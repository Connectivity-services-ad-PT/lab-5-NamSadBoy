from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx
import paho.mqtt.client as mqtt
import psycopg
from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


SERVICE_NAME = os.getenv("SERVICE_NAME", "core-business")
SERVICE_VERSION = os.getenv("SERVICE_VERSION", "1.0.0")
AUTH_TOKEN = os.getenv("AUTH_TOKEN", "lab-core-token")
DECISION_TTL_SECONDS = int(os.getenv("ACCESS_DECISION_TTL_SECONDS", "30"))
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://lab05:lab05pass@db:5432/coredb",
)
AUDIT_SERVICE_URL = os.getenv("AUDIT_SERVICE_URL", "http://audit-service:9000")
NOTIFICATION_SERVICE_URL = os.getenv(
    "NOTIFICATION_SERVICE_URL",
    "http://partner-service:9100",
).rstrip("/")
NOTIFICATION_PATH = os.getenv("NOTIFICATION_PATH", "/api/v1/notifications")
if not NOTIFICATION_PATH.startswith("/"):
    NOTIFICATION_PATH = f"/{NOTIFICATION_PATH}"
NOTIFICATION_AUTH_TOKEN = os.getenv("NOTIFICATION_AUTH_TOKEN") or None
ANALYTICS_SERVICE_URL = os.getenv(
    "ANALYTICS_SERVICE_URL",
    "http://partner-service:9100",
).rstrip("/")
ANALYTICS_PATH = os.getenv("ANALYTICS_PATH", "/api/v1/events")
if not ANALYTICS_PATH.startswith("/"):
    ANALYTICS_PATH = f"/{ANALYTICS_PATH}"
ANALYTICS_AUTH_TOKEN = os.getenv("ANALYTICS_AUTH_TOKEN") or None
ACCESS_GATE_SERVICE_URL = os.getenv(
    "ACCESS_GATE_SERVICE_URL",
    "http://partner-service:9100",
).rstrip("/")
ACCESS_GATE_PATH = os.getenv("ACCESS_GATE_PATH", "/api/v1/access-logs/query")
if not ACCESS_GATE_PATH.startswith("/"):
    ACCESS_GATE_PATH = f"/{ACCESS_GATE_PATH}"
ACCESS_GATE_AUTH_TOKEN = os.getenv("ACCESS_GATE_AUTH_TOKEN") or None
ACCESS_GATE_METHOD = os.getenv("ACCESS_GATE_METHOD", "POST").upper()
PARTNER_TIMEOUT_SECONDS = float(os.getenv("PARTNER_TIMEOUT_SECONDS", "3"))
PARTNER_RETRY_COUNT = int(os.getenv("PARTNER_RETRY_COUNT", "0"))
MQTT_ENABLED = os.getenv("MQTT_ENABLED", "true").lower() == "true"
MQTT_HOST = os.getenv("MQTT_HOST", "mqtt-broker")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_TLS = os.getenv("MQTT_TLS", "false").lower() == "true"
MQTT_USERNAME = os.getenv("MQTT_USERNAME") or None
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD") or None
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "smart-campus/events/sensor")
MQTT_ACCESS_TOPIC = os.getenv("MQTT_ACCESS_TOPIC", "smart-campus/events/access")
MQTT_CAMERA_TOPIC = os.getenv("MQTT_CAMERA_TOPIC", "smart-campus/events/camera")
MQTT_ALERT_TOPIC = os.getenv("MQTT_ALERT_TOPIC", "smart-campus/events/alert")
MQTT_CORE_TOPIC = os.getenv("MQTT_CORE_TOPIC", "smart-campus/events/core")
MQTT_OUTBOUND_ENABLED = os.getenv("MQTT_OUTBOUND_ENABLED", "true").lower() == "true"
MQTT_QOS = int(os.getenv("MQTT_QOS", "1"))
MQTT_CLIENT_ID = os.getenv("MQTT_CLIENT_ID", "team-core-core-business")
IOT_SOURCE_SERVICE = os.getenv("IOT_SOURCE_SERVICE", "a1-iot-ingestion")
ALERT_DEDUP_WINDOW_SECONDS = int(os.getenv("ALERT_DEDUP_WINDOW_SECONDS", "300"))
CORRELATION_WINDOW_SECONDS = int(os.getenv("CORRELATION_WINDOW_SECONDS", "120"))
DENIED_ACCESS_WINDOW_SECONDS = int(os.getenv("DENIED_ACCESS_WINDOW_SECONDS", "300"))
DENIED_ACCESS_THRESHOLD = int(os.getenv("DENIED_ACCESS_THRESHOLD", "3"))

ACCESS_POLICY_ID = "7e34be8b-1da8-483e-b5ae-28f8662d0ac7"
SENSOR_POLICY_ID = "98ae19f6-13f6-4cc5-aa8f-3b76cb8c68ec"
DETECTION_POLICY_ID = "26a2f3c5-32ce-4c1d-b66e-d3e6a900989e"


app = FastAPI(
    title="Smart Campus Core Business Policy API",
    version=SERVICE_VERSION,
    description="Policy evaluation for access, sensor, and AI Vision events.",
)


class ProblemError(Exception):
    def __init__(self, status_code: int, title: str, detail: str, problem_type: str):
        self.status_code = status_code
        self.title = title
        self.detail = detail
        self.problem_type = problem_type


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def trace_id(request: Request) -> str:
    candidate = request.headers.get("X-Correlation-Id")
    try:
        return str(UUID(candidate)) if candidate else str(uuid4())
    except ValueError:
        return str(uuid4())


def problem_response(
    request: Request,
    status_code: int,
    title: str,
    detail: str,
    problem_type: str,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        media_type="application/problem+json",
        content={
            "type": f"https://smart-campus.example/problems/{problem_type}",
            "title": title,
            "status": status_code,
            "detail": detail,
            "instance": request.url.path,
            "traceId": trace_id(request),
        },
    )


@app.exception_handler(ProblemError)
async def handle_problem(request: Request, exc: ProblemError) -> JSONResponse:
    return problem_response(
        request,
        exc.status_code,
        exc.title,
        exc.detail,
        exc.problem_type,
    )


@app.exception_handler(RequestValidationError)
async def handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
    first_error = exc.errors()[0] if exc.errors() else {}
    location = ".".join(str(part) for part in first_error.get("loc", []))
    message = first_error.get("msg", "Request does not match the contract.")
    detail = f"{location}: {message}" if location else message
    return problem_response(
        request,
        422,
        "Unprocessable Entity",
        detail,
        "validation-error",
    )


def require_auth(authorization: str | None = Header(default=None)) -> None:
    if authorization != f"Bearer {AUTH_TOKEN}":
        raise ProblemError(
            401,
            "Unauthorized",
            "A valid bearer token is required.",
            "unauthorized",
        )


class Direction(str, Enum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"


class Role(str, Enum):
    STUDENT = "STUDENT"
    STAFF = "STAFF"
    SECURITY = "SECURITY"
    VISITOR = "VISITOR"


class CardStatus(str, Enum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"
    SUSPENDED = "SUSPENDED"


class AccessSubject(BaseModel):
    subjectId: str = Field(min_length=3, max_length=64)
    role: Role
    cardStatus: CardStatus
    zone: str = Field(min_length=2, max_length=32)


class AccessEvaluationRequest(BaseModel):
    requestId: UUID
    cardId: str = Field(pattern=r"^CARD-[A-Z0-9]{6,20}$")
    gateId: str = Field(pattern=r"^GATE-[A-Z0-9-]{2,20}$")
    direction: Direction
    occurredAt: datetime
    subject: AccessSubject


class AccessGateLogQuery(BaseModel):
    requestId: UUID
    cardId: str = Field(pattern=r"^CARD-[A-Z0-9]{6,20}$")
    gateId: str = Field(pattern=r"^GATE-[A-Z0-9-]{2,20}$")
    direction: Direction
    from_: datetime = Field(alias="from")
    to: datetime
    limit: int = Field(default=20, ge=1, le=100)


class SensorMetric(str, Enum):
    TEMPERATURE = "TEMPERATURE"
    HUMIDITY = "HUMIDITY"
    SMOKE = "SMOKE"
    CO2 = "CO2"


class SensorUnit(str, Enum):
    CELSIUS = "CELSIUS"
    PERCENT = "PERCENT"
    PPM = "PPM"
    BOOLEAN = "BOOLEAN"


class SensorEvaluationRequest(BaseModel):
    requestId: UUID
    deviceId: str = Field(pattern=r"^SENSOR-[A-Z0-9-]{2,24}$")
    metric: SensorMetric
    value: float = Field(ge=-100, le=10000)
    unit: SensorUnit
    occurredAt: datetime


class DetectionLabel(str, Enum):
    AUTHORIZED_PERSON = "AUTHORIZED_PERSON"
    UNKNOWN_PERSON = "UNKNOWN_PERSON"
    CROWD = "CROWD"
    FIRE = "FIRE"
    SMOKE = "SMOKE"


class DetectionEvaluationRequest(BaseModel):
    requestId: UUID
    detectionId: UUID
    cameraId: str = Field(pattern=r"^CAMERA-[A-Z0-9-]{2,24}$")
    label: DetectionLabel
    confidence: float = Field(ge=0, le=1)
    occurredAt: datetime
    location: str | None = Field(default=None, min_length=1, max_length=128)


class VisionResultRequest(BaseModel):
    request_id: str = Field(min_length=3, max_length=128)
    camera_id: str = Field(min_length=2, max_length=64)
    location: str = Field(min_length=1, max_length=128)
    analysis: dict[str, Any] = Field(default_factory=dict)
    labels: list[Any] = Field(default_factory=list)
    risk_level: str = Field(default="medium", min_length=2, max_length=32)
    summary: str = Field(min_length=1, max_length=500)


class PolicyCreateRequest(BaseModel):
    name: str = Field(min_length=3, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    priority: int = Field(ge=1, le=1000)
    active: bool
    rules: list[dict[str, Any]] = Field(min_length=1, max_length=20)


policies: dict[str, dict[str, Any]] = {
    ACCESS_POLICY_ID: {
        "policyId": ACCESS_POLICY_ID,
        "name": "Staff office-hours access",
        "description": "Allow active staff cards during office hours.",
        "priority": 100,
        "active": True,
        "version": 1,
        "createdAt": "2026-05-19T08:00:00Z",
        "rules": [
            {"ruleType": "ROLE", "allowedRoles": ["STAFF", "SECURITY"]},
            {
                "ruleType": "TIME_WINDOW",
                "timezone": "Asia/Ho_Chi_Minh",
                "start": "06:00",
                "end": "22:00",
            },
        ],
    }
}
decisions: dict[str, dict[str, Any]] = {}
alerts: list[dict[str, Any]] = []
idempotency_results: dict[str, dict[str, Any]] = {}
mqtt_events: list[dict[str, Any]] = []
mqtt_client: mqtt.Client | None = None
mqtt_connected = False


def db_connection() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL, autocommit=True)


def init_database() -> None:
    last_error: Exception | None = None
    for _ in range(30):
        try:
            with db_connection() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS decisions (
                        decision_id UUID PRIMARY KEY,
                        payload JSONB NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS alerts (
                        alert_id UUID PRIMARY KEY,
                        payload JSONB NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS processed_events (
                        idempotency_key TEXT PRIMARY KEY,
                        result JSONB NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS event_facts (
                        fact_id UUID PRIMARY KEY,
                        event_type TEXT NOT NULL,
                        source_event_id TEXT NOT NULL,
                        location TEXT,
                        occurred_at TIMESTAMPTZ NOT NULL,
                        payload JSONB NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        UNIQUE (event_type, source_event_id)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_event_facts_type_time
                    ON event_facts (event_type, occurred_at DESC)
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS alert_dedup (
                        dedup_key TEXT PRIMARY KEY,
                        last_alert_at TIMESTAMPTZ NOT NULL,
                        alert_id UUID
                    )
                    """
                )
            return
        except Exception as exc:
            last_error = exc
            time.sleep(1)
    raise RuntimeError(f"Database initialization failed: {last_error}")


@app.on_event("startup")
def startup() -> None:
    init_database()
    start_mqtt_subscriber()


@app.on_event("shutdown")
def shutdown() -> None:
    stop_mqtt_subscriber()


def persist_decision(result: dict[str, Any]) -> None:
    with db_connection() as connection:
        connection.execute(
            """
            INSERT INTO decisions (decision_id, payload)
            VALUES (%s, %s::jsonb)
            ON CONFLICT (decision_id) DO UPDATE SET payload = EXCLUDED.payload
            """,
            (result["decisionId"], json.dumps(result)),
        )


def persist_alert(alert: dict[str, Any]) -> None:
    with db_connection() as connection:
        connection.execute(
            """
            INSERT INTO alerts (alert_id, payload)
            VALUES (%s, %s::jsonb)
            ON CONFLICT (alert_id) DO UPDATE SET payload = EXCLUDED.payload
            """,
            (alert["alertId"], json.dumps(alert)),
        )


def publish_audit_event(event: dict[str, Any]) -> None:
    try:
        httpx.post(f"{AUDIT_SERVICE_URL}/events", json=event, timeout=2).raise_for_status()
    except httpx.HTTPError:
        # The database remains the source of truth; the event can be replayed later.
        pass


def dependencies_ready() -> bool:
    try:
        with db_connection() as connection:
            connection.execute("SELECT 1").fetchone()
        response = httpx.get(f"{AUDIT_SERVICE_URL}/health", timeout=2)
        return response.status_code == 200
    except (psycopg.Error, httpx.HTTPError):
        return False


def claim_alert_slot(dedup_key: str, now: datetime, window_seconds: int) -> bool:
    cutoff = now - timedelta(seconds=window_seconds)
    with db_connection() as connection:
        claimed = connection.execute(
            """
            INSERT INTO alert_dedup (dedup_key, last_alert_at)
            VALUES (%s, %s)
            ON CONFLICT (dedup_key) DO UPDATE
            SET last_alert_at = EXCLUDED.last_alert_at
            WHERE alert_dedup.last_alert_at <= %s
            RETURNING dedup_key
            """,
            (dedup_key, now, cutoff),
        ).fetchone()
    return claimed is not None


def create_alert(
    decision_id: str,
    severity: str,
    message: str,
    *,
    alert_type: str = "policy",
    location: str | None = None,
    evidence_event_ids: list[str] | None = None,
    dedup_key: str | None = None,
    dedup_window_seconds: int = ALERT_DEDUP_WINDOW_SECONDS,
) -> str | None:
    now = utc_now()
    if dedup_key and not claim_alert_slot(dedup_key, now, dedup_window_seconds):
        publish_audit_event(
            {
                "eventType": "alert.suppressed.duplicate",
                "payload": {
                    "decisionId": decision_id,
                    "dedupKey": dedup_key,
                    "suppressedAt": iso(now),
                },
            }
        )
        return None

    alert_id = str(uuid4())
    alert = {
        "alertId": alert_id,
        "decisionId": decision_id,
        "alertType": alert_type,
        "severity": severity.upper(),
        "status": "OPEN",
        "message": message,
        "location": location,
        "evidenceEventIds": evidence_event_ids or [],
        "dedupKey": dedup_key,
        "createdAt": iso(now),
    }
    alerts.append(alert)
    persist_alert(alert)
    publish_audit_event({"eventType": "alert.created", "payload": alert})
    return alert_id


def cached(idempotency_key: str | None) -> dict[str, Any] | None:
    if not idempotency_key:
        return None
    in_memory = idempotency_results.get(idempotency_key)
    if in_memory:
        return in_memory
    try:
        with db_connection() as connection:
            row = connection.execute(
                "SELECT result FROM processed_events WHERE idempotency_key = %s",
                (idempotency_key,),
            ).fetchone()
        if row:
            idempotency_results[idempotency_key] = row[0]
            return row[0]
    except psycopg.Error:
        return None
    return None


def remember(idempotency_key: str | None, result: dict[str, Any]) -> None:
    if idempotency_key:
        idempotency_results[idempotency_key] = result
        try:
            with db_connection() as connection:
                connection.execute(
                    """
                    INSERT INTO processed_events (idempotency_key, result)
                    VALUES (%s, %s::jsonb)
                    ON CONFLICT (idempotency_key) DO NOTHING
                    """,
                    (idempotency_key, json.dumps(result)),
                )
        except psycopg.Error:
            pass


def record_event_fact(
    event_type: str,
    source_event_id: str,
    location: str | None,
    occurred_at: datetime,
    payload: dict[str, Any],
) -> None:
    fact_id = uuid5(NAMESPACE_URL, f"{event_type}:{source_event_id}")
    normalized_location = canonical_location(location) if location else None
    with db_connection() as connection:
        connection.execute(
            """
            INSERT INTO event_facts (
                fact_id, event_type, source_event_id, location, occurred_at, payload
            )
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (event_type, source_event_id) DO NOTHING
            """,
            (
                str(fact_id),
                event_type,
                source_event_id,
                normalized_location,
                occurred_at,
                json.dumps(payload),
            ),
        )


def recent_event_facts(
    event_type: str,
    occurred_at: datetime,
    window_seconds: int,
) -> list[dict[str, Any]]:
    window_start = occurred_at - timedelta(seconds=window_seconds)
    window_end = occurred_at + timedelta(seconds=window_seconds)
    with db_connection() as connection:
        rows = connection.execute(
            """
            SELECT source_event_id, location, occurred_at, payload
            FROM event_facts
            WHERE event_type = %s AND occurred_at BETWEEN %s AND %s
            ORDER BY occurred_at DESC
            """,
            (event_type, window_start, window_end),
        ).fetchall()
    return [
        {
            "sourceEventId": row[0],
            "location": row[1],
            "occurredAt": iso(row[2]),
            "payload": row[3],
        }
        for row in rows
    ]


def canonical_location(location: str) -> str:
    normalized = re.sub(r"[^A-Z0-9]+", "-", location.upper()).strip("-")
    gate_match = re.search(r"GATE-([A-Z]|\d{1,2})", normalized)
    if gate_match:
        gate_token = gate_match.group(1)
        if gate_token.isalpha():
            gate_token = f"{ord(gate_token) - ord('A') + 1:02d}"
        else:
            gate_token = f"{int(gate_token):02d}"
        return f"GATE-{gate_token}"
    return normalized or "UNKNOWN"


def apply_repeated_access_policy(
    payload: AccessEvaluationRequest,
    result: dict[str, Any],
) -> None:
    fact_payload = {
        "input": payload.model_dump(mode="json"),
        "result": result,
    }
    record_event_fact(
        "access",
        str(payload.requestId),
        payload.gateId,
        payload.occurredAt,
        fact_payload,
    )
    if result["decision"] != "DENY":
        return

    recent_facts = recent_event_facts(
        "access",
        payload.occurredAt,
        DENIED_ACCESS_WINDOW_SECONDS,
    )
    matching_denials = [
        fact
        for fact in recent_facts
        if fact["payload"].get("result", {}).get("decision") == "DENY"
        and fact["payload"].get("input", {}).get("cardId") == payload.cardId
        and fact["payload"].get("input", {}).get("gateId") == payload.gateId
    ]
    result["deniedAttemptsInWindow"] = len(matching_denials)
    if len(matching_denials) < DENIED_ACCESS_THRESHOLD:
        return

    original_reason = result["reasonCode"]
    alert_id = create_alert(
        result["decisionId"],
        "MEDIUM",
        (
            f"Repeated denied access for {payload.cardId} at {payload.gateId}: "
            f"{len(matching_denials)} attempts within "
            f"{DENIED_ACCESS_WINDOW_SECONDS} seconds."
        ),
        alert_type="suspicious_access",
        location=payload.gateId,
        evidence_event_ids=[fact["sourceEventId"] for fact in matching_denials],
        dedup_key=f"repeated-access:{payload.cardId.lower()}:{payload.gateId.lower()}",
        dedup_window_seconds=DENIED_ACCESS_WINDOW_SECONDS,
    )
    result["underlyingReasonCode"] = original_reason
    result["reasonCode"] = "REPEATED_ACCESS_DENIED"
    result["alertId"] = alert_id
    result["severity"] = "MEDIUM"
    result["alertSuppressed"] = alert_id is None


class IotMqttSensorEvent(BaseModel):
    eventId: str = Field(min_length=3, max_length=128)
    eventType: str = Field(min_length=3, max_length=128)
    sourceService: str = Field(min_length=3, max_length=64)
    timestamp: datetime = Field(default_factory=utc_now)
    rawEventId: str | None = Field(default=None, min_length=3, max_length=128)
    deviceId: str = Field(min_length=3, max_length=64)
    location: str = Field(min_length=1, max_length=128)
    temperatureC: float | None = Field(default=None, ge=-100, le=200)
    humidityPercent: float | None = Field(default=None, ge=0, le=100)
    motionDetected: bool | None = None
    lightLux: float | None = Field(default=None, ge=0, le=200000)
    co2Ppm: float | None = Field(default=None, ge=0, le=100000)
    smokePpm: float | None = Field(default=None, ge=0, le=100000)
    batteryPercent: float | None = Field(default=None, ge=0, le=100)
    status: str = Field(min_length=2, max_length=32)
    alertLevel: str = Field(min_length=2, max_length=32)
    reason: str = Field(min_length=2, max_length=128)


class AccessMqttEvent(BaseModel):
    event_type: str = Field(default="access.swipe.processed", min_length=3, max_length=128)
    source_service: str = Field(default="team-gate", min_length=3, max_length=64)
    raw_event_id: str = Field(min_length=3, max_length=128)
    timestamp: datetime = Field(default_factory=utc_now)
    uid: str = Field(min_length=3, max_length=64)
    student_id: str | None = Field(default=None, max_length=64)
    full_name: str | None = Field(default=None, max_length=128)
    class_name: str | None = Field(default=None, max_length=64)
    door_id: str = Field(min_length=2, max_length=64)
    location: str = Field(min_length=1, max_length=128)
    direction: str = Field(pattern=r"^(in|out|ENTRY|EXIT)$")
    access_result: str = Field(pattern=r"^(granted|denied)$")
    reason: str = Field(min_length=2, max_length=128)


class CameraMqttEvent(BaseModel):
    request_id: str = Field(min_length=3, max_length=128)
    event_type: str = Field(default="camera.motion.triggered", min_length=3, max_length=128)
    source_service: str = Field(default="team-camera", min_length=3, max_length=64)
    camera_id: str = Field(min_length=2, max_length=64)
    timestamp: datetime
    location: str = Field(min_length=1, max_length=128)
    motion_detected: bool
    motion_score: float = Field(ge=0, le=1)
    snapshot_url: str | None = Field(default=None, max_length=2048)


def normalize_sensor_id(device_id: str) -> str:
    normalized = re.sub(r"[^A-Z0-9-]+", "-", device_id.upper()).strip("-")
    normalized = normalized[:17] or "UNKNOWN"
    return f"SENSOR-{normalized}"


def normalize_camera_id(camera_id: str) -> str:
    normalized = re.sub(r"[^A-Z0-9-]+", "-", camera_id.upper()).strip("-")
    normalized = normalized[:17] or "UNKNOWN"
    return f"CAMERA-{normalized}"


def iot_event_to_sensor_request(event: IotMqttSensorEvent) -> SensorEvaluationRequest:
    event_reason = event.reason.lower()
    if "smoke" in event_reason:
        metric = SensorMetric.SMOKE
        value = event.smokePpm if event.smokePpm and event.smokePpm > 0 else 1
        unit = SensorUnit.PPM
    elif event.temperatureC is not None and (
        "temperature" in event_reason or event.temperatureC >= 35
    ):
        metric = SensorMetric.TEMPERATURE
        value = event.temperatureC
        unit = SensorUnit.CELSIUS
    elif event.smokePpm is not None and (event.smokePpm > 0 or "smoke" in event_reason):
        metric = SensorMetric.SMOKE
        value = event.smokePpm
        unit = SensorUnit.PPM
    elif event.co2Ppm is not None and ("co2" in event_reason or event.co2Ppm >= 1000):
        metric = SensorMetric.CO2
        value = event.co2Ppm
        unit = SensorUnit.PPM
    else:
        metric = SensorMetric.HUMIDITY
        value = event.humidityPercent or 0
        unit = SensorUnit.PERCENT

    raw_event_id = event.rawEventId or event.eventId
    request_id = uuid5(NAMESPACE_URL, f"{event.sourceService}:{event.eventId}:{raw_event_id}")
    return SensorEvaluationRequest(
        requestId=request_id,
        deviceId=normalize_sensor_id(event.deviceId),
        metric=metric,
        value=value,
        unit=unit,
        occurredAt=event.timestamp,
    )


def is_after_hours(occurred_at: datetime) -> bool:
    local_time = occurred_at.astimezone(timezone(timedelta(hours=7)))
    return local_time.hour < 7 or local_time.hour >= 18


def is_access_policy_time(occurred_at: datetime) -> bool:
    local_time = occurred_at.astimezone(timezone(timedelta(hours=7)))
    return 6 <= local_time.hour < 22


def classify_iot_sensor_event(
    event: IotMqttSensorEvent,
    payload: SensorEvaluationRequest,
) -> tuple[str, str, str]:
    event_status = event.status.lower()
    event_reason = event.reason.lower()
    alert_level = event.alertLevel.lower()

    if event_status == "invalid_device" or event_reason == "device_not_registered":
        return "ALERT", "IOT_INVALID_DEVICE", "HIGH"
    if event_status == "sensor_error" or event_reason in {
        "missing_sensor_value",
        "invalid_sensor_value",
    }:
        return "ALERT", "IOT_SENSOR_ERROR", "HIGH"
    if event_reason == "smoke_detected" or (
        "smoke" in event_reason and event.smokePpm is not None and event.smokePpm >= 1
    ):
        return "ALERT", "IOT_SMOKE_DETECTED_CRITICAL", "CRITICAL"
    if event.motionDetected and is_after_hours(event.timestamp):
        return "ALERT", "IOT_MOTION_DETECTED_OUT_OF_HOURS", "HIGH"
    if event_status == "danger":
        severity = "CRITICAL" if alert_level == "critical" else "HIGH"
        return "ALERT", "IOT_STATUS_DANGER", severity
    if event_status == "warning":
        return "WARNING", "IOT_STATUS_WARNING", "MEDIUM"
    if payload.metric == SensorMetric.SMOKE and payload.value >= 1:
        return "ALERT", "SENSOR_THRESHOLD_CRITICAL", "CRITICAL"
    if payload.metric == SensorMetric.TEMPERATURE and payload.value >= 40:
        return "ALERT", "SENSOR_THRESHOLD_CRITICAL", "HIGH"
    if payload.metric == SensorMetric.TEMPERATURE and payload.value >= 35:
        return "WARNING", "SENSOR_THRESHOLD_WARNING", "MEDIUM"
    return "NORMAL", "SENSOR_NORMAL", "LOW"


def evaluate_iot_sensor_event(
    event: IotMqttSensorEvent,
    payload: SensorEvaluationRequest,
    idempotency_key: str | None,
) -> dict[str, Any]:
    previous = cached(idempotency_key)
    if previous:
        return previous

    outcome, reason, severity = classify_iot_sensor_event(event, payload)

    decision_id = str(uuid4())
    alert_id = (
        create_alert(
            decision_id,
            severity,
            (
                f"IoT {event.status} event for {event.deviceId} at {event.location}: "
                f"{event.reason}."
            ),
            alert_type="environment",
            location=event.location,
            evidence_event_ids=[event.eventId],
            dedup_key=(
                f"environment:{reason}:{event.location.lower()}:{event.deviceId.lower()}"
            ),
        )
        if outcome != "NORMAL"
        else None
    )
    result = {
        "decisionId": decision_id,
        "requestId": str(payload.requestId),
        "outcome": outcome,
        "reasonCode": reason,
        "policyId": SENSOR_POLICY_ID,
        "alertId": alert_id,
        "severity": severity,
        "alertSuppressed": outcome != "NORMAL" and alert_id is None,
        "evaluatedAt": iso(utc_now()),
        "iotEventId": event.eventId,
        "deviceId": event.deviceId,
        "location": event.location,
        "status": event.status,
        "alertLevel": event.alertLevel,
        "reason": event.reason,
    }
    decisions[decision_id] = result
    persist_decision(result)
    publish_audit_event({"eventType": "policy.decision.created", "payload": result})
    remember(idempotency_key, result)
    return result


def record_mqtt_event(record: dict[str, Any]) -> None:
    mqtt_events.append(record)
    del mqtt_events[:-100]


def process_iot_mqtt_message(topic: str, qos: int, payload_bytes: bytes) -> None:
    received_at = iso(utc_now())
    try:
        raw_payload = json.loads(payload_bytes.decode("utf-8"))
        event = IotMqttSensorEvent.model_validate(raw_payload)
        if event.sourceService != IOT_SOURCE_SERVICE:
            record_mqtt_event(
                {
                    "status": "ignored_source",
                    "topic": topic,
                    "qos": qos,
                    "mqttEventId": event.eventId,
                    "sourceService": event.sourceService,
                    "expectedSourceService": IOT_SOURCE_SERVICE,
                    "receivedAt": received_at,
                }
            )
            return

        sensor_request = iot_event_to_sensor_request(event)
        record_event_fact(
            "sensor",
            event.eventId,
            event.location,
            event.timestamp,
            event.model_dump(mode="json"),
        )
        print(
            (
                f"received sensor event from {event.sourceService} "
                f"deviceId={event.deviceId} status={event.status} reason={event.reason}"
            ),
            flush=True,
        )
        result = evaluate_iot_sensor_event(event, sensor_request, f"mqtt:{event.eventId}")
        if result.get("alertId"):
            print(f"created alert alertId={result['alertId']}", flush=True)
        else:
            print(f"processed sensor event without alert outcome={result['outcome']}", flush=True)
        correlation_id = str(uuid5(NAMESPACE_URL, f"mqtt:{event.eventId}"))
        status, deliveries = mqtt_partner_deliveries(
            "sensor-event",
            sensor_request,
            result,
            correlation_id,
        )

        record = {
            "status": status,
            "topic": topic,
            "qos": qos,
            "mqttEventId": event.eventId,
            "rawEventId": event.rawEventId,
            "sourceService": event.sourceService,
            "receivedAt": received_at,
            "payload": event.model_dump(mode="json"),
            "normalizedRequest": sensor_request.model_dump(mode="json"),
            "result": result,
            "deliveries": deliveries,
        }
        record_mqtt_event(record)
        publish_audit_event({"eventType": "mqtt.sensor.received", "payload": record})
    except Exception as exc:
        record_mqtt_event(
            {
                "status": "invalid",
                "topic": topic,
                "qos": qos,
                "receivedAt": received_at,
                "error": f"{exc.__class__.__name__}: {exc}",
                "payload": payload_bytes.decode("utf-8", errors="replace"),
            }
        )


def mqtt_partner_deliveries(
    event_type: str,
    source_payload: BaseModel,
    result: dict[str, Any],
    correlation_id: str,
) -> tuple[str, dict[str, Any]]:
    try:
        return (
            "processed",
            deliver_integration_result(
                event_type,
                source_payload,
                result,
                correlation_id,
                strict=False,
            ),
        )
    except ProblemError as exc:
        return (
            "processed_partner_failed",
            {
                "error": {
                    "status": exc.status_code,
                    "title": exc.title,
                    "detail": exc.detail,
                }
            },
        )


def process_access_mqtt_message(topic: str, qos: int, payload_bytes: bytes) -> None:
    received_at = iso(utc_now())
    try:
        event = AccessMqttEvent.model_validate_json(payload_bytes)
        decision_id = str(uuid4())
        denied = event.access_result == "denied"
        result: dict[str, Any] = {
            "decisionId": decision_id,
            "requestId": event.raw_event_id,
            "outcome": "DENIED" if denied else "NORMAL",
            "reasonCode": event.reason.upper(),
            "policyId": ACCESS_POLICY_ID,
            "alertId": None,
            "severity": "LOW",
            "evaluatedAt": iso(utc_now()),
            "accessResult": event.access_result,
        }
        record_event_fact(
            "access",
            event.raw_event_id,
            event.location,
            event.timestamp,
            {"input": event.model_dump(mode="json"), "result": result},
        )

        if denied:
            recent_denials = [
                fact
                for fact in recent_event_facts(
                    "access",
                    event.timestamp,
                    DENIED_ACCESS_WINDOW_SECONDS,
                )
                if fact["payload"].get("result", {}).get("accessResult") == "denied"
                and fact["payload"].get("input", {}).get("uid") == event.uid
                and fact["payload"].get("input", {}).get("door_id") == event.door_id
            ]
            result["deniedAttemptsInWindow"] = len(recent_denials)
            if len(recent_denials) >= DENIED_ACCESS_THRESHOLD:
                result["reasonCode"] = "REPEATED_ACCESS_DENIED"
                result["severity"] = "MEDIUM"
                result["alertId"] = create_alert(
                    decision_id,
                    "MEDIUM",
                    (
                        f"Repeated denied RFID access for {event.uid} at {event.location}: "
                        f"{len(recent_denials)} attempts."
                    ),
                    alert_type="suspicious_access",
                    location=event.location,
                    evidence_event_ids=[
                        fact["sourceEventId"] for fact in recent_denials
                    ],
                    dedup_key=f"repeated-access:{event.uid.lower()}:{event.door_id.lower()}",
                    dedup_window_seconds=DENIED_ACCESS_WINDOW_SECONDS,
                )
                result["alertSuppressed"] = result["alertId"] is None

        decisions[decision_id] = result
        persist_decision(result)
        publish_audit_event({"eventType": "policy.decision.created", "payload": result})
        correlation_id = str(uuid5(NAMESPACE_URL, f"mqtt:{event.raw_event_id}"))
        status, deliveries = mqtt_partner_deliveries(
            "access-event",
            event,
            result,
            correlation_id,
        )
        record = {
            "status": status,
            "topic": topic,
            "qos": qos,
            "mqttEventId": event.raw_event_id,
            "sourceService": event.source_service,
            "receivedAt": received_at,
            "payload": event.model_dump(mode="json"),
            "result": result,
            "deliveries": deliveries,
        }
        record_mqtt_event(record)
        publish_audit_event({"eventType": "mqtt.access.received", "payload": record})
    except Exception as exc:
        record_mqtt_event(
            {
                "status": "invalid",
                "topic": topic,
                "qos": qos,
                "receivedAt": received_at,
                "error": f"{exc.__class__.__name__}: {exc}",
                "payload": payload_bytes.decode("utf-8", errors="replace"),
            }
        )


def process_camera_mqtt_message(topic: str, qos: int, payload_bytes: bytes) -> None:
    received_at = iso(utc_now())
    try:
        event = CameraMqttEvent.model_validate_json(payload_bytes)
        record_event_fact(
            "camera",
            event.request_id,
            event.location,
            event.timestamp,
            event.model_dump(mode="json"),
        )
        recent_granted_access = [
            fact
            for fact in recent_event_facts(
                "access",
                event.timestamp,
                CORRELATION_WINDOW_SECONDS,
            )
            if fact["location"] == canonical_location(event.location)
            and (
                fact["payload"].get("result", {}).get("decision") == "ALLOW"
                or fact["payload"].get("result", {}).get("accessResult") == "granted"
            )
        ]
        suspicious_motion = (
            event.motion_detected
            and is_after_hours(event.timestamp)
            and not recent_granted_access
        )
        decision_id = str(uuid4())
        outcome = "ALERT" if suspicious_motion else "NORMAL"
        reason = (
            "CAMERA_MOTION_OUTSIDE_HOURS_NO_VALID_ACCESS"
            if suspicious_motion
            else "CAMERA_EVENT_RECORDED"
        )
        severity = "HIGH" if suspicious_motion else "LOW"
        alert_id = (
            create_alert(
                decision_id,
                severity,
                f"Unexpected motion detected by {event.camera_id} at {event.location}.",
                alert_type="security",
                location=event.location,
                evidence_event_ids=[event.request_id],
                dedup_key=(
                    f"camera-motion:{canonical_location(event.location).lower()}"
                ),
            )
            if suspicious_motion
            else None
        )
        result = {
            "decisionId": decision_id,
            "requestId": event.request_id,
            "outcome": outcome,
            "reasonCode": reason,
            "policyId": DETECTION_POLICY_ID,
            "alertId": alert_id,
            "severity": severity,
            "alertSuppressed": suspicious_motion and alert_id is None,
            "evaluatedAt": iso(utc_now()),
        }
        decisions[decision_id] = result
        persist_decision(result)
        publish_audit_event({"eventType": "policy.decision.created", "payload": result})
        correlation_id = str(uuid5(NAMESPACE_URL, f"mqtt:{event.request_id}"))
        status, deliveries = mqtt_partner_deliveries(
            "camera-event",
            event,
            result,
            correlation_id,
        )
        record = {
            "status": status,
            "topic": topic,
            "qos": qos,
            "mqttEventId": event.request_id,
            "sourceService": event.source_service,
            "receivedAt": received_at,
            "payload": event.model_dump(mode="json"),
            "result": result,
            "deliveries": deliveries,
        }
        record_mqtt_event(record)
        publish_audit_event({"eventType": "mqtt.camera.received", "payload": record})
    except Exception as exc:
        record_mqtt_event(
            {
                "status": "invalid",
                "topic": topic,
                "qos": qos,
                "receivedAt": received_at,
                "error": f"{exc.__class__.__name__}: {exc}",
                "payload": payload_bytes.decode("utf-8", errors="replace"),
            }
        )


def process_mqtt_message(topic: str, qos: int, payload_bytes: bytes) -> None:
    if topic == MQTT_TOPIC:
        process_iot_mqtt_message(topic, qos, payload_bytes)
    elif topic == MQTT_ACCESS_TOPIC:
        process_access_mqtt_message(topic, qos, payload_bytes)
    elif topic == MQTT_CAMERA_TOPIC:
        process_camera_mqtt_message(topic, qos, payload_bytes)
    else:
        record_mqtt_event(
            {
                "status": "ignored_topic",
                "topic": topic,
                "qos": qos,
                "receivedAt": iso(utc_now()),
            }
        )


def start_mqtt_subscriber() -> None:
    global mqtt_client
    if not MQTT_ENABLED:
        return

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=MQTT_CLIENT_ID,
        protocol=mqtt.MQTTv311,
    )
    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    if MQTT_TLS:
        client.tls_set()

    def on_connect(
        client: mqtt.Client,
        userdata: Any,
        flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
    ) -> None:
        global mqtt_connected
        mqtt_connected = reason_code == 0
        if mqtt_connected:
            for topic in {MQTT_TOPIC, MQTT_ACCESS_TOPIC, MQTT_CAMERA_TOPIC}:
                client.subscribe(topic, qos=MQTT_QOS)

    def on_disconnect(
        client: mqtt.Client,
        userdata: Any,
        disconnect_flags: mqtt.DisconnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
    ) -> None:
        global mqtt_connected
        mqtt_connected = False

    def on_message(client: mqtt.Client, userdata: Any, message: mqtt.MQTTMessage) -> None:
        process_mqtt_message(message.topic, message.qos, message.payload)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()
    mqtt_client = client


def stop_mqtt_subscriber() -> None:
    global mqtt_client, mqtt_connected
    if mqtt_client:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
    mqtt_client = None
    mqtt_connected = False


def publish_mqtt_event(topic: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not MQTT_OUTBOUND_ENABLED:
        return {"status": "skipped", "topic": topic, "reason": "outbound_disabled"}
    if not mqtt_client or not mqtt_connected:
        return {"status": "unavailable", "topic": topic, "reason": "mqtt_disconnected"}
    try:
        info = mqtt_client.publish(topic, json.dumps(payload), qos=MQTT_QOS)
        info.wait_for_publish(timeout=PARTNER_TIMEOUT_SECONDS)
        return {"status": "published", "topic": topic, "qos": MQTT_QOS}
    except (RuntimeError, ValueError, OSError) as exc:
        return {
            "status": "failed",
            "topic": topic,
            "reason": f"{exc.__class__.__name__}: {exc}",
        }


def deliver_to_partner(
    provider: str,
    base_url: str,
    path: str,
    payload: dict[str, Any],
    correlation_id: str,
    auth_token: str | None = None,
    method: str = "POST",
    query_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    attempts = PARTNER_RETRY_COUNT + 1
    last_detail = "Partner service is unavailable."
    request_method = method.upper()

    for attempt in range(1, attempts + 1):
        try:
            headers = {"X-Correlation-Id": correlation_id}
            if auth_token:
                headers["Authorization"] = (
                    auth_token if auth_token.lower().startswith("bearer ") else f"Bearer {auth_token}"
                )
            if request_method == "GET":
                response = httpx.get(
                    f"{base_url}{path}",
                    params=query_params or payload,
                    headers=headers,
                    timeout=PARTNER_TIMEOUT_SECONDS,
                )
            elif request_method == "POST":
                response = httpx.post(
                    f"{base_url}{path}",
                    json=payload,
                    headers=headers,
                    timeout=PARTNER_TIMEOUT_SECONDS,
                )
            else:
                raise ProblemError(
                    500,
                    "Invalid partner method",
                    f"{provider} method {request_method} is not supported.",
                    "invalid-partner-method",
                )
            response.raise_for_status()
            body = response.json() if response.content else {}
            return {
                "provider": provider,
                "status": "accepted",
                "statusCode": response.status_code,
                "method": request_method,
                "providerResponse": body,
            }
        except httpx.TimeoutException:
            last_detail = (
                f"{provider} timed out after {PARTNER_TIMEOUT_SECONDS:g} seconds "
                f"on attempt {attempt}/{attempts}."
            )
        except httpx.HTTPStatusError as exc:
            last_detail = (
                f"{provider} returned HTTP {exc.response.status_code} "
                f"on attempt {attempt}/{attempts}."
            )
        except httpx.RequestError as exc:
            last_detail = (
                f"Cannot connect to {provider} on attempt {attempt}/{attempts}: "
                f"{exc.__class__.__name__}."
            )

    raise ProblemError(
        503,
        "Dependent service unavailable",
        last_detail,
        "dependency-unavailable",
    )


def deliver_integration_result(
    event_type: str,
    source_payload: BaseModel,
    result: dict[str, Any],
    correlation_id: str,
    strict: bool = True,
) -> dict[str, Any]:
    analytics_payload = {
        "eventId": str(uuid4()),
        "eventType": f"core.{event_type}.processed",
        "source": SERVICE_NAME,
        "occurredAt": iso(utc_now()),
        "correlationId": correlation_id,
        "payload": {
            "input": source_payload.model_dump(mode="json"),
            "result": result,
        },
    }
    errors: list[ProblemError] = []
    deliveries: dict[str, Any] = {
        "analyticsMqtt": publish_mqtt_event(MQTT_CORE_TOPIC, analytics_payload),
        "notification": {"provider": "notification", "status": "skipped"},
        "notificationMqtt": {"status": "skipped", "topic": MQTT_ALERT_TOPIC},
    }
    try:
        deliveries["analytics"] = deliver_to_partner(
            "analytics",
            ANALYTICS_SERVICE_URL,
            ANALYTICS_PATH,
            analytics_payload,
            correlation_id,
            ANALYTICS_AUTH_TOKEN,
        )
    except ProblemError as exc:
        errors.append(exc)
        deliveries["analytics"] = {
            "provider": "analytics",
            "status": "failed",
            "statusCode": exc.status_code,
            "detail": exc.detail,
        }

    if result.get("alertId"):
        notification_id = str(uuid4())
        occurred_at = iso(utc_now())
        title = f"Core policy alert: {event_type}"
        message = result.get("reasonCode", "Policy alert generated.")
        severity = str(result.get("severity", "HIGH")).upper()
        channels_by_severity = {
            "CRITICAL": ["telegram", "email", "app"],
            "HIGH": ["telegram", "app"],
            "MEDIUM": ["email"],
            "LOW": [],
        }
        channels = channels_by_severity.get(severity, ["email"])
        notification_payload = {
            "eventId": notification_id,
            "notificationId": notification_id,
            "eventType": "alert.created",
            "source": "core-business-service",
            "sourceService": "team-core",
            "channel": "MULTI" if len(channels) > 1 else (channels[0].upper() if channels else "LOG"),
            "channels": channels,
            "severity": severity,
            "alertVersion": 1,
            "title": title,
            "message": message,
            "correlationId": correlation_id,
            "timestamp": occurred_at,
            "createdAt": occurred_at,
            "occurredAt": occurred_at,
            "alertId": result["alertId"],
            "recipientGroup": "security-ops",
            "data": {
                "title": title,
                "message": message,
                "source": "core-business-service",
            },
            "metadata": {
                "alertId": result["alertId"],
                "decisionId": result["decisionId"],
            },
        }
        deliveries["notificationMqtt"] = publish_mqtt_event(
            MQTT_ALERT_TOPIC,
            notification_payload,
        )
        try:
            deliveries["notification"] = deliver_to_partner(
                "notification",
                NOTIFICATION_SERVICE_URL,
                NOTIFICATION_PATH,
                notification_payload,
                correlation_id,
                NOTIFICATION_AUTH_TOKEN,
            )
        except ProblemError as exc:
            errors.append(exc)
            deliveries["notification"] = {
                "provider": "notification",
                "status": "failed",
                "statusCode": exc.status_code,
                "detail": exc.detail,
            }

    if strict and errors:
        raise errors[0]

    return deliveries


def integration_response(
    event_type: str,
    source_payload: BaseModel,
    result: dict[str, Any],
    correlation_id: str,
) -> dict[str, Any]:
    return {
        "eventType": event_type,
        "status": "processed",
        "correlationId": correlation_id,
        "result": result,
        "deliveries": deliver_integration_result(
            event_type,
            source_payload,
            result,
            correlation_id,
        ),
    }


@app.get("/health", tags=["Health"])
def health() -> dict[str, str]:
    if not dependencies_ready():
        raise ProblemError(
            503,
            "Service Unavailable",
            "Database or audit service is not ready.",
            "dependency-unavailable",
        )
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "version": SERVICE_VERSION,
        "policyStore": "ready",
    }


def http_partner_health(name: str, base_url: str) -> dict[str, Any]:
    try:
        response = httpx.get(f"{base_url}/health", timeout=PARTNER_TIMEOUT_SECONDS)
        return {
            "name": name,
            "ok": response.status_code < 500,
            "statusCode": response.status_code,
            "url": base_url,
        }
    except httpx.TimeoutException:
        return {
            "name": name,
            "ok": False,
            "url": base_url,
            "error": f"timeout after {PARTNER_TIMEOUT_SECONDS:g} seconds",
        }
    except httpx.RequestError as exc:
        return {
            "name": name,
            "ok": False,
            "url": base_url,
            "error": exc.__class__.__name__,
        }


@app.get("/partners/health", tags=["Health"])
def partners_health() -> dict[str, Any]:
    partners = [
        http_partner_health("notification", NOTIFICATION_SERVICE_URL),
        http_partner_health("analytics", ANALYTICS_SERVICE_URL),
        http_partner_health("access-gate", ACCESS_GATE_SERVICE_URL),
        {
            "name": "mqtt",
            "ok": (not MQTT_ENABLED) or mqtt_connected,
            "enabled": MQTT_ENABLED,
            "host": MQTT_HOST,
            "port": MQTT_PORT,
            "topics": [MQTT_TOPIC, MQTT_ACCESS_TOPIC, MQTT_CAMERA_TOPIC],
            "outboundTopics": [MQTT_ALERT_TOPIC, MQTT_CORE_TOPIC],
            "qos": MQTT_QOS,
        },
    ]
    return {"ok": all(partner["ok"] for partner in partners), "partners": partners}


@app.get("/mqtt/status", tags=["Integration"])
def get_mqtt_status() -> dict[str, Any]:
    return {
        "enabled": MQTT_ENABLED,
        "connected": mqtt_connected,
        "host": MQTT_HOST,
        "port": MQTT_PORT,
        "topic": MQTT_TOPIC,
        "topics": [MQTT_TOPIC, MQTT_ACCESS_TOPIC, MQTT_CAMERA_TOPIC],
        "outboundTopics": [MQTT_ALERT_TOPIC, MQTT_CORE_TOPIC],
        "outboundEnabled": MQTT_OUTBOUND_ENABLED,
        "iotSourceService": IOT_SOURCE_SERVICE,
        "qos": MQTT_QOS,
        "receivedCount": len(mqtt_events),
    }


@app.get("/mqtt/events", tags=["Integration"], dependencies=[Depends(require_auth)])
def list_mqtt_events(limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
    items = list(reversed(mqtt_events))[:limit]
    return {"items": items, "count": len(items)}


@app.delete("/mqtt/events", tags=["Integration"], dependencies=[Depends(require_auth)])
def clear_mqtt_events() -> dict[str, str]:
    mqtt_events.clear()
    return {"status": "cleared"}


@app.post(
    "/policies/evaluate-access",
    tags=["Policies"],
    dependencies=[Depends(require_auth)],
)
def evaluate_access(
    payload: AccessEvaluationRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
) -> dict[str, Any]:
    previous = cached(idempotency_key)
    if previous:
        return previous

    status_reason = {
        CardStatus.EXPIRED: "POLICY_DENY_EXPIRED_CARD",
        CardStatus.REVOKED: "POLICY_DENY_REVOKED",
        CardStatus.SUSPENDED: "POLICY_DENY_SUSPENDED",
    }
    if payload.subject.cardStatus != CardStatus.ACTIVE:
        decision = "DENY"
        reason = status_reason[payload.subject.cardStatus]
        explanation = f"Card status is {payload.subject.cardStatus.value}."
    elif payload.subject.role not in {Role.STAFF, Role.SECURITY}:
        decision = "DENY"
        reason = "POLICY_DENY_ROLE"
        explanation = f"Role {payload.subject.role.value} is not allowed."
    elif payload.subject.zone != "ADMIN":
        decision = "DENY"
        reason = "POLICY_DENY_ZONE"
        explanation = f"Zone {payload.subject.zone} is not allowed."
    elif not is_access_policy_time(payload.occurredAt):
        decision = "DENY"
        reason = "POLICY_DENY_OUTSIDE_ALLOWED_HOURS"
        explanation = "Access is outside the configured 06:00-22:00 window."
    else:
        decision = "ALLOW"
        reason = "POLICY_ALLOW"
        explanation = "Active staff card is allowed in ADMIN zone."

    now = utc_now()
    decision_id = str(uuid4())
    result = {
        "decisionId": decision_id,
        "requestId": str(payload.requestId),
        "decision": decision,
        "reasonCode": reason,
        "policyId": ACCESS_POLICY_ID,
        "evaluatedAt": iso(now),
        "expiresAt": iso(now + timedelta(seconds=DECISION_TTL_SECONDS))
        if decision == "ALLOW"
        else None,
        "correlationId": correlation_id or str(uuid4()),
        "explanation": explanation,
        "alertId": None,
        "severity": "LOW",
    }
    apply_repeated_access_policy(payload, result)
    decisions[decision_id] = result
    persist_decision(result)
    publish_audit_event({"eventType": "policy.decision.created", "payload": result})
    remember(idempotency_key, result)
    return result


@app.post(
    "/policies/evaluate-sensor",
    tags=["Policies"],
    dependencies=[Depends(require_auth)],
)
def evaluate_sensor(
    payload: SensorEvaluationRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    previous = cached(idempotency_key)
    if previous:
        return previous

    record_event_fact(
        "sensor",
        str(payload.requestId),
        payload.deviceId,
        payload.occurredAt,
        payload.model_dump(mode="json"),
    )

    if payload.metric == SensorMetric.SMOKE and payload.value >= 1:
        outcome, reason, severity = "ALERT", "SENSOR_THRESHOLD_CRITICAL", "CRITICAL"
    elif payload.metric == SensorMetric.SMOKE and payload.value >= 0.5:
        outcome, reason, severity = "WARNING", "SENSOR_THRESHOLD_WARNING", "MEDIUM"
    elif payload.metric == SensorMetric.TEMPERATURE and payload.value >= 40:
        outcome, reason, severity = "ALERT", "SENSOR_THRESHOLD_CRITICAL", "HIGH"
    elif payload.metric == SensorMetric.TEMPERATURE and payload.value >= 35:
        outcome, reason, severity = "WARNING", "SENSOR_THRESHOLD_WARNING", "MEDIUM"
    else:
        outcome, reason, severity = "NORMAL", "SENSOR_NORMAL", "LOW"

    decision_id = str(uuid4())
    alert_id = (
        create_alert(
            decision_id,
            severity,
            f"{payload.metric.value} policy outcome {outcome} for {payload.deviceId}.",
            alert_type="environment",
            location=payload.deviceId,
            evidence_event_ids=[str(payload.requestId)],
            dedup_key=f"sensor:{payload.metric.value.lower()}:{payload.deviceId.lower()}",
        )
        if outcome != "NORMAL"
        else None
    )
    result = {
        "decisionId": decision_id,
        "requestId": str(payload.requestId),
        "outcome": outcome,
        "reasonCode": reason,
        "policyId": SENSOR_POLICY_ID,
        "alertId": alert_id,
        "severity": severity,
        "alertSuppressed": outcome != "NORMAL" and alert_id is None,
        "evaluatedAt": iso(utc_now()),
    }
    decisions[decision_id] = result
    persist_decision(result)
    publish_audit_event({"eventType": "policy.decision.created", "payload": result})
    remember(idempotency_key, result)
    return result


@app.post(
    "/policies/evaluate-detection",
    tags=["Policies"],
    dependencies=[Depends(require_auth)],
)
def evaluate_detection(
    payload: DetectionEvaluationRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    previous = cached(idempotency_key)
    if previous:
        return previous

    record_event_fact(
        "vision",
        str(payload.detectionId),
        payload.location or payload.cameraId,
        payload.occurredAt,
        payload.model_dump(mode="json"),
    )
    recent_denials: list[dict[str, Any]] = []
    if payload.location:
        expected_location = canonical_location(payload.location)
        recent_denials = [
            fact
            for fact in recent_event_facts(
                "access",
                payload.occurredAt,
                CORRELATION_WINDOW_SECONDS,
            )
            if fact["location"] == expected_location
            and fact["payload"].get("result", {}).get("decision") == "DENY"
        ]

    if payload.label in {DetectionLabel.FIRE, DetectionLabel.SMOKE}:
        outcome, reason, severity = "ALERT", "HAZARD_DETECTED", "CRITICAL"
    elif (
        payload.label == DetectionLabel.UNKNOWN_PERSON
        and payload.confidence >= 0.8
        and recent_denials
    ):
        outcome, reason, severity = "ALERT", "INTRUSION_CORRELATED", "CRITICAL"
    elif payload.label == DetectionLabel.UNKNOWN_PERSON and payload.confidence >= 0.8:
        outcome, reason, severity = "ALERT", "UNKNOWN_PERSON_HIGH_CONFIDENCE", "HIGH"
    elif payload.label == DetectionLabel.CROWD and payload.confidence >= 0.8:
        outcome, reason, severity = "REVIEW", "CROWD_DETECTED", "MEDIUM"
    elif payload.label == DetectionLabel.AUTHORIZED_PERSON:
        outcome, reason, severity = "IGNORE", "AUTHORIZED_PERSON", "LOW"
    else:
        outcome, reason, severity = "IGNORE", "LOW_CONFIDENCE", "LOW"

    decision_id = str(uuid4())
    evidence_event_ids = [str(payload.detectionId)] + [
        fact["sourceEventId"] for fact in recent_denials
    ]
    alert_id = (
        create_alert(
            decision_id,
            severity,
            f"{payload.label.value} detected by {payload.cameraId}.",
            alert_type="security",
            location=payload.location or payload.cameraId,
            evidence_event_ids=evidence_event_ids,
            dedup_key=(
                f"vision:{reason}:"
                f"{canonical_location(payload.location or payload.cameraId).lower()}"
            ),
        )
        if outcome == "ALERT"
        else None
    )
    result = {
        "decisionId": decision_id,
        "requestId": str(payload.requestId),
        "outcome": outcome,
        "reasonCode": reason,
        "policyId": DETECTION_POLICY_ID,
        "alertId": alert_id,
        "severity": severity,
        "alertSuppressed": outcome == "ALERT" and alert_id is None,
        "correlatedEvidenceEventIds": [
            fact["sourceEventId"] for fact in recent_denials
        ],
        "evaluatedAt": iso(utc_now()),
    }
    decisions[decision_id] = result
    persist_decision(result)
    publish_audit_event({"eventType": "policy.decision.created", "payload": result})
    remember(idempotency_key, result)
    return result


def vision_result_to_detection_request(payload: VisionResultRequest) -> DetectionEvaluationRequest:
    raw_labels = payload.labels or payload.analysis.get("labels") or []
    labels = set()
    for raw_label in raw_labels:
        label_value = raw_label.get("label") if isinstance(raw_label, dict) else raw_label
        if label_value:
            labels.add(str(label_value).upper().replace(" ", "_").replace("-", "_"))
    summary = payload.summary.lower()
    risk_level = payload.risk_level.lower()

    if labels & {"FIRE", "FLAME"} or "fire" in summary:
        label = DetectionLabel.FIRE
    elif labels & {"SMOKE"} or "smoke" in summary:
        label = DetectionLabel.SMOKE
    elif labels & {"UNKNOWN_PERSON", "STRANGER", "INTRUDER", "PERSON"}:
        label = DetectionLabel.UNKNOWN_PERSON
    elif labels & {"CROWD", "CROWD_DETECTED"}:
        label = DetectionLabel.CROWD
    else:
        label = DetectionLabel.AUTHORIZED_PERSON

    confidence = payload.analysis.get("confidence")
    if confidence is None:
        confidence = payload.analysis.get("score")
    if confidence is None:
        confidence = 0.95 if risk_level in {"high", "critical"} else 0.7
    confidence = max(0, min(1, float(confidence)))

    request_id = uuid5(NAMESPACE_URL, f"vision:{payload.request_id}")
    detection_id = uuid5(
        NAMESPACE_URL,
        f"vision:{payload.request_id}:{payload.camera_id}:{payload.summary}",
    )
    occurred_at = payload.analysis.get("timestamp") or payload.analysis.get("occurredAt")
    return DetectionEvaluationRequest(
        requestId=request_id,
        detectionId=detection_id,
        cameraId=normalize_camera_id(payload.camera_id),
        label=label,
        confidence=confidence,
        occurredAt=datetime.fromisoformat(occurred_at) if occurred_at else utc_now(),
        location=payload.location,
    )


@app.post(
    "/api/v1/access-events",
    tags=["Integration"],
    dependencies=[Depends(require_auth)],
)
def ingest_access_event(
    payload: AccessEvaluationRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
) -> dict[str, Any]:
    request_correlation_id = correlation_id or str(uuid4())
    result = evaluate_access(payload, idempotency_key, request_correlation_id)
    return integration_response(
        "access-event",
        payload,
        result,
        request_correlation_id,
    )


@app.post(
    "/api/v1/access-gate/log-query",
    tags=["Integration"],
    dependencies=[Depends(require_auth)],
)
def query_access_gate_logs(
    payload: AccessGateLogQuery,
    correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
) -> dict[str, Any]:
    request_correlation_id = correlation_id or str(uuid4())
    payload_json = payload.model_dump(mode="json", by_alias=True)
    query_params = {"limit": payload.limit} if ACCESS_GATE_METHOD == "GET" else None
    delivery = deliver_to_partner(
        "access-gate",
        ACCESS_GATE_SERVICE_URL,
        ACCESS_GATE_PATH,
        payload_json,
        request_correlation_id,
        ACCESS_GATE_AUTH_TOKEN,
        ACCESS_GATE_METHOD,
        query_params,
    )
    return {
        "eventType": "access-gate-log-query",
        "status": "processed",
        "correlationId": request_correlation_id,
        "request": payload_json,
        "delivery": delivery,
    }


@app.post(
    "/api/v1/sensor-events",
    status_code=202,
    tags=["Integration"],
    dependencies=[Depends(require_auth)],
)
def ingest_sensor_event(
    payload: SensorEvaluationRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
) -> dict[str, Any]:
    request_correlation_id = correlation_id or str(uuid4())
    result = evaluate_sensor(payload, idempotency_key)
    return integration_response(
        "sensor-event",
        payload,
        result,
        request_correlation_id,
    )


@app.post(
    "/api/v1/detections",
    status_code=202,
    tags=["Integration"],
    dependencies=[Depends(require_auth)],
)
def ingest_detection_event(
    payload: DetectionEvaluationRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
) -> dict[str, Any]:
    request_correlation_id = correlation_id or str(uuid4())
    result = evaluate_detection(payload, idempotency_key)
    return integration_response(
        "detection",
        payload,
        result,
        request_correlation_id,
    )


@app.post(
    "/api/v1/vision-results",
    status_code=202,
    tags=["Integration"],
    dependencies=[Depends(require_auth)],
)
def ingest_vision_result(
    payload: VisionResultRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
) -> dict[str, Any]:
    request_correlation_id = correlation_id or str(uuid4())
    detection_payload = vision_result_to_detection_request(payload)
    result = evaluate_detection(detection_payload, idempotency_key)
    response = integration_response(
        "vision-result",
        detection_payload,
        result,
        request_correlation_id,
    )
    response["sourceVisionResult"] = payload.model_dump(mode="json")
    return response


@app.post("/policies", status_code=201, tags=["Policies"], dependencies=[Depends(require_auth)])
def create_policy(payload: PolicyCreateRequest) -> dict[str, Any]:
    policy_id = str(uuid4())
    policy = {
        "policyId": policy_id,
        **payload.model_dump(),
        "version": 1,
        "createdAt": iso(utc_now()),
    }
    policies[policy_id] = policy
    return policy


@app.get("/policies", tags=["Policies"], dependencies=[Depends(require_auth)])
def list_policies(
    active: bool | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    items = list(policies.values())
    if active is not None:
        items = [item for item in items if item["active"] is active]
    items = items[:limit]
    return {"items": items, "count": len(items)}


@app.get("/policies/{policy_id}", tags=["Policies"], dependencies=[Depends(require_auth)])
def get_policy(policy_id: UUID) -> dict[str, Any]:
    policy = policies.get(str(policy_id))
    if not policy:
        raise ProblemError(404, "Resource not found", "Policy does not exist.", "not-found")
    return policy


@app.get("/decisions/{decision_id}", tags=["Decisions"], dependencies=[Depends(require_auth)])
def get_decision(decision_id: UUID) -> dict[str, Any]:
    decision = decisions.get(str(decision_id))
    if not decision:
        raise ProblemError(404, "Resource not found", "Decision does not exist.", "not-found")
    return decision


@app.get("/alerts", tags=["Alerts"], dependencies=[Depends(require_auth)])
def list_alerts(
    status: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    items = alerts
    if status:
        items = [item for item in items if item["status"] == status]
    items = list(reversed(items))[:limit]
    return {"items": items, "count": len(items)}
