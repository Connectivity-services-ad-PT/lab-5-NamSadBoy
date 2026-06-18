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
MQTT_QOS = int(os.getenv("MQTT_QOS", "1"))
MQTT_CLIENT_ID = os.getenv("MQTT_CLIENT_ID", "team-core-core-business")

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


def create_alert(decision_id: str, severity: str, message: str) -> str:
    alert_id = str(uuid4())
    alert = {
        "alertId": alert_id,
        "decisionId": decision_id,
        "severity": severity,
        "status": "OPEN",
        "message": message,
        "createdAt": iso(utc_now()),
    }
    alerts.append(alert)
    persist_alert(alert)
    publish_audit_event({"eventType": "alert.created", "payload": alert})
    return alert_id


def cached(idempotency_key: str | None) -> dict[str, Any] | None:
    return idempotency_results.get(idempotency_key) if idempotency_key else None


def remember(idempotency_key: str | None, result: dict[str, Any]) -> None:
    if idempotency_key:
        idempotency_results[idempotency_key] = result


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


def evaluate_iot_sensor_event(
    event: IotMqttSensorEvent,
    payload: SensorEvaluationRequest,
    idempotency_key: str | None,
) -> dict[str, Any]:
    previous = cached(idempotency_key)
    if previous:
        return previous

    event_status = event.status.lower()
    event_reason = event.reason.lower()
    alert_level = event.alertLevel.lower()

    if "smoke_detected" in event_reason or (
        "smoke" in event_reason and event.smokePpm is not None and event.smokePpm > 0
    ):
        outcome, reason, severity = "ALERT", "IOT_SMOKE_DETECTED_CRITICAL", "CRITICAL"
    elif event.motionDetected and is_after_hours(event.timestamp):
        outcome, reason, severity = "ALERT", "IOT_MOTION_DETECTED_OUT_OF_HOURS", "HIGH"
    elif event_status == "danger" or alert_level in {"high", "critical"}:
        outcome, reason, severity = "ALERT", "IOT_STATUS_DANGER", "CRITICAL"
    elif event_status == "warning" or alert_level in {"medium", "warning"}:
        outcome, reason, severity = "WARNING", "IOT_STATUS_WARNING", "MEDIUM"
    elif payload.metric == SensorMetric.SMOKE and payload.value > 0:
        outcome, reason, severity = "ALERT", "SENSOR_THRESHOLD_CRITICAL", "CRITICAL"
    elif payload.metric == SensorMetric.TEMPERATURE and payload.value >= 40:
        outcome, reason, severity = "ALERT", "SENSOR_THRESHOLD_CRITICAL", "CRITICAL"
    elif payload.metric == SensorMetric.TEMPERATURE and payload.value >= 35:
        outcome, reason, severity = "WARNING", "SENSOR_THRESHOLD_WARNING", "MEDIUM"
    else:
        outcome, reason, severity = "NORMAL", "SENSOR_NORMAL", "LOW"

    decision_id = str(uuid4())
    alert_id = (
        create_alert(
            decision_id,
            severity,
            (
                f"IoT {event.status} event for {event.deviceId} at {event.location}: "
                f"{event.reason}."
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
        sensor_request = iot_event_to_sensor_request(event)
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
        status = "processed"
        try:
            deliveries: dict[str, Any] = deliver_integration_result(
                "sensor-event",
                sensor_request,
                result,
                correlation_id,
            )
        except ProblemError as exc:
            status = "processed_partner_failed"
            deliveries = {
                "error": {
                    "status": exc.status_code,
                    "title": exc.title,
                    "detail": exc.detail,
                }
            }

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
            client.subscribe(MQTT_TOPIC, qos=MQTT_QOS)

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
        process_iot_mqtt_message(message.topic, message.qos, message.payload)

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
    deliveries: dict[str, Any] = {
        "analytics": deliver_to_partner(
            "analytics",
            ANALYTICS_SERVICE_URL,
            ANALYTICS_PATH,
            analytics_payload,
            correlation_id,
            ANALYTICS_AUTH_TOKEN,
        ),
        "notification": {"provider": "notification", "status": "skipped"},
    }

    if result.get("alertId"):
        notification_id = str(uuid4())
        notification_payload = {
            "eventId": notification_id,
            "notificationId": notification_id,
            "eventType": "core.alert.created",
            "source": SERVICE_NAME,
            "sourceService": "team-core",
            "channel": "MULTI",
            "severity": "HIGH",
            "title": f"Core policy alert: {event_type}",
            "message": result.get("reasonCode", "Policy alert generated."),
            "correlationId": correlation_id,
            "timestamp": iso(utc_now()),
            "createdAt": iso(utc_now()),
            "alertId": result["alertId"],
            "recipientGroup": "security-ops",
            "metadata": {
                "alertId": result["alertId"],
                "decisionId": result["decisionId"],
            },
        }
        deliveries["notification"] = deliver_to_partner(
            "notification",
            NOTIFICATION_SERVICE_URL,
            NOTIFICATION_PATH,
            notification_payload,
            correlation_id,
            NOTIFICATION_AUTH_TOKEN,
        )

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
            "topic": MQTT_TOPIC,
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
    }
    decisions[decision_id] = result
    persist_decision(result)
    publish_audit_event({"eventType": "policy.decision.created", "payload": result})
    if decision == "DENY":
        create_alert(
            decision_id,
            "HIGH",
            f"{payload.cardId} denied at {payload.gateId}: {reason}",
        )
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

    if payload.metric == SensorMetric.SMOKE and payload.value > 0:
        outcome, reason = "ALERT", "SENSOR_THRESHOLD_CRITICAL"
    elif payload.metric == SensorMetric.TEMPERATURE and payload.value >= 40:
        outcome, reason = "ALERT", "SENSOR_THRESHOLD_CRITICAL"
    elif payload.metric == SensorMetric.TEMPERATURE and payload.value >= 35:
        outcome, reason = "WARNING", "SENSOR_THRESHOLD_WARNING"
    else:
        outcome, reason = "NORMAL", "SENSOR_NORMAL"

    decision_id = str(uuid4())
    alert_id = (
        create_alert(
            decision_id,
            "CRITICAL" if outcome == "ALERT" else "MEDIUM",
            f"{payload.metric.value} policy outcome {outcome} for {payload.deviceId}.",
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

    if payload.label in {DetectionLabel.FIRE, DetectionLabel.SMOKE}:
        outcome, reason = "ALERT", "HAZARD_DETECTED"
    elif payload.label == DetectionLabel.UNKNOWN_PERSON and payload.confidence >= 0.8:
        outcome, reason = "ALERT", "UNKNOWN_PERSON_HIGH_CONFIDENCE"
    elif payload.label == DetectionLabel.CROWD and payload.confidence >= 0.8:
        outcome, reason = "REVIEW", "CROWD_DETECTED"
    elif payload.label == DetectionLabel.AUTHORIZED_PERSON:
        outcome, reason = "IGNORE", "AUTHORIZED_PERSON"
    else:
        outcome, reason = "IGNORE", "LOW_CONFIDENCE"

    decision_id = str(uuid4())
    alert_id = (
        create_alert(
            decision_id,
            "HIGH",
            f"{payload.label.value} detected by {payload.cameraId}.",
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
