# Integration Agreements - Team Core Business

Base URL in class: `http://<CORE_DEMO_IP>:8000`

All inbound requests include:

```http
Authorization: Bearer <AUTH_TOKEN>
Content-Type: application/json
Idempotency-Key: <unique-key-for-the-event>
X-Correlation-Id: <uuid>
```

## IoT Ingestion to Core

- `POST /api/v1/sensor-events`
- Success: `202 Accepted`

```json
{"requestId":"fd680d46-f0dd-4b32-aadf-6a0f51612251","deviceId":"SENSOR-B601","metric":"TEMPERATURE","value":42.5,"unit":"CELSIUS","occurredAt":"2026-06-13T08:01:00Z"}
```

## AI Vision to Core

- `POST /api/v1/detections`
- Success: `202 Accepted`

```json
{"requestId":"8de888d9-a133-473e-a0d9-060af8e911af","detectionId":"8d424c77-2298-48f5-8b28-291eea122bcb","cameraId":"CAMERA-B6-01","label":"UNKNOWN_PERSON","confidence":0.96,"occurredAt":"2026-06-13T08:02:00Z"}
```

## Access Gate to Core

- `POST /api/v1/access-events`
- Success: `200 OK`

```json
{"requestId":"40fd107d-d34a-4f48-833d-26335f6a18ec","cardId":"CARD-060001","gateId":"GATE-A1","direction":"ENTRY","occurredAt":"2026-06-13T08:00:00Z","subject":{"subjectId":"EMP-0601","role":"STAFF","cardStatus":"ACTIVE","zone":"ADMIN"}}
```

## Core to Notification

- Provider URL: `${NOTIFICATION_SERVICE_URL}`
- `POST /api/v1/notifications`
- Expected success: any `2xx`, recommended `202`
- Called only when Core creates an alert.

## Core to Analytics

- Provider URL: `${ANALYTICS_SERVICE_URL}`
- `POST /api/v1/events`
- Expected success: any `2xx`, recommended `202`
- Called for every processed access, sensor, or detection event.

## Failure agreement

Core waits at most `PARTNER_TIMEOUT_SECONDS` (default 3 seconds). A timeout,
connection error, or non-2xx partner response produces HTTP `503` with
`application/problem+json`. The consumer retries with the same
`Idempotency-Key`; Core reuses the existing policy decision.

Hotspot health test completed: `[ ]` (check in class with a second laptop)

