# Integration Agreements - Team Core Business

Copy-paste appointment forms for provider teams:
[`phieu-hen-tich-hop-team-core-providers.md`](phieu-hen-tich-hop-team-core-providers.md).
Shared Radmin IP sheet:
[`radmin-ip-sheet.md`](radmin-ip-sheet.md).

Base URL in class or remote test: `http://<RADMIN_IP_TEAM_CORE>:8000`

Use the Radmin IP for REST calls between laptops. Do not use `localhost`,
Wi-Fi/hotspot IP, or Docker service names across different machines. MQTT
contracts still use the agreed broker URL.

All inbound requests include:

```http
Authorization: Bearer <AUTH_TOKEN>
Content-Type: application/json
Idempotency-Key: <unique-key-for-the-event>
X-Correlation-Id: <uuid>
```

## IoT Ingestion to Core

- Provider URL: `mqtts://f6f78e87db4a4c189dd3d706745a5e93.s1.eu.hivemq.cloud:8883`
- Method: `MQTT PUBLISH`
- Topic: `smart-campus/events/sensor`
- QoS: `1`
- Success evidence: Core subscribes to the topic and records the event in `GET /mqtt/events`.

```json
{
  "eventId": "sensor-event-001",
  "eventType": "sensor.reading.processed",
  "sourceService": "team-iot",
  "timestamp": "2026-06-17T14:30:10+07:00",
  "rawEventId": "raw-iot-abc123",
  "deviceId": "esp32-lab-a101",
  "location": "Lab A101",
  "temperatureC": 42.1,
  "humidityPercent": 71.2,
  "motionDetected": true,
  "lightLux": 390,
  "co2Ppm": 710,
  "smokePpm": 0.03,
  "batteryPercent": 86,
  "status": "danger",
  "alertLevel": "high",
  "reason": "temperature_too_high"
}
```

Core maps this payload to an internal `SensorEvaluationRequest`, evaluates the
temperature policy, creates an alert, and forwards the result to Analytics and
Notification when configured.

Core accepts the minimal team-iot camelCase payload even when `timestamp` and
`rawEventId` are omitted. Policy rules:

- `status=danger` creates an `ALERT`.
- `status=warning` creates a `WARNING` alert.
- `reason=smoke_detected` creates a critical alert.
- `motionDetected=true` outside 07:00-18:00 creates an unusual-motion alert.

Expected Core log:

```text
received sensor event from team-iot deviceId=esp32-lab-a101 status=danger reason=temperature_too_high
created alert alertId=<uuid>
```

## AI Vision to Core

- `POST /api/v1/detections`
- Success: `202 Accepted`

```json
{"requestId":"8de888d9-a133-473e-a0d9-060af8e911af","detectionId":"8d424c77-2298-48f5-8b28-291eea122bcb","cameraId":"CAMERA-B6-01","label":"UNKNOWN_PERSON","confidence":0.96,"occurredAt":"2026-06-13T08:02:00Z"}
```

Team Vision can also send its current result callback payload to Core:

- `POST /api/v1/vision-results`
- Success: `202 Accepted`
- Headers: `Authorization: Bearer <AUTH_TOKEN>`, `Content-Type: application/json`

```json
{
  "request_id": "vision-request-001",
  "camera_id": "cam-01",
  "location": "Gate A",
  "analysis": {
    "confidence": 0.96,
    "timestamp": "2026-06-18T09:10:00+07:00"
  },
  "labels": ["unknown_person"],
  "risk_level": "high",
  "summary": "Unknown person detected at Gate A"
}
```

Core maps this to detection policy, creates a policy decision/alert when needed,
and forwards the result to Analytics and Notification when configured.

## Access Gate to Core

- `POST /api/v1/access-events`
- Success: `200 OK`

```json
{"requestId":"40fd107d-d34a-4f48-833d-26335f6a18ec","cardId":"CARD-060001","gateId":"GATE-A1","direction":"ENTRY","occurredAt":"2026-06-13T08:00:00Z","subject":{"subjectId":"EMP-0601","role":"STAFF","cardStatus":"ACTIVE","zone":"ADMIN"}}
```

## Core to Access Gate

- Core test endpoint: `POST /api/v1/access-gate/log-query`
- Provider URL: `${ACCESS_GATE_SERVICE_URL}`
- Provider path: `${ACCESS_GATE_PATH}` (default `/api/v1/access-logs/query`)
- Provider method: `${ACCESS_GATE_METHOD}` (default `POST`; team Gate uses `GET`)
- Auth: `Authorization: Bearer ${ACCESS_GATE_AUTH_TOKEN}` when configured

Core receives a log query request, forwards it to Access Gate, and returns the
provider response for evidence.

Team Gate live configuration:

```env
ACCESS_GATE_SERVICE_URL=http://26.150.185.206:8000
ACCESS_GATE_PATH=/access-events
ACCESS_GATE_AUTH_TOKEN=local-dev-token
ACCESS_GATE_METHOD=GET
```

```json
{
  "requestId": "73dcbb01-069a-4b6b-9d19-3ec3c80c1340",
  "cardId": "CARD-060001",
  "gateId": "GATE-A1",
  "direction": "ENTRY",
  "from": "2026-06-18T09:00:00+07:00",
  "to": "2026-06-18T10:00:00+07:00",
  "limit": 20
}
```

## Core to Notification

- Provider URL: `${NOTIFICATION_SERVICE_URL}`
- Path: `${NOTIFICATION_PATH}` (default `/api/v1/notifications`)
- Auth: `Authorization: Bearer ${NOTIFICATION_AUTH_TOKEN}` when configured
- Live endpoint: `POST http://26.95.36.20:8000/events/alert.created`
- Live auth: `Authorization: Bearer local-dev-token`

Core sends alert payloads compatible with team A7 Notification:

```json
{
  "eventId": "333aee5c-4164-44d5-b3aa-6c60572ccb40",
  "eventType": "alert.created",
  "alertId": "677bc548-9efc-49d6-a96a-3abd8a2bdedd",
  "correlationId": "COR-2026-05-19-001",
  "source": "core-business-service",
  "severity": "HIGH",
  "alertVersion": 1,
  "occurredAt": "2026-06-18T01:23:38Z",
  "data": {
    "title": "Core policy alert",
    "message": "Policy alert generated.",
    "source": "core-business-service"
  },
  "channels": ["telegram", "email", "app"]
}
```
- Expected success: any `2xx`, recommended `202`
- Called only when Core creates an alert.

## Core to Analytics

- Provider URL: `${ANALYTICS_SERVICE_URL}`
- Path: `${ANALYTICS_PATH}` (default `/api/v1/events`)
- Auth: `Authorization: Bearer ${ANALYTICS_AUTH_TOKEN}` when configured

Team Analytics live configuration:

```env
ANALYTICS_SERVICE_URL=http://26.22.249.37:8000
ANALYTICS_PATH=/events/core
ANALYTICS_AUTH_TOKEN=local-dev-token
```
- Expected success: any `2xx`, recommended `202`
- Called for every processed access, sensor, or detection event.

## Failure agreement

REST partner calls wait at most `PARTNER_TIMEOUT_SECONDS` (default 3 seconds).
A timeout, connection error, or non-2xx partner response produces HTTP `503`
with `application/problem+json`. MQTT publish is asynchronous: IoT uses QoS 1
and Core verifies subscription/topic/credential through `/mqtt/status`,
`/mqtt/events`, and `/partners/health`.

Hotspot health test completed: `[ ]` (check in class with a second laptop)
