# Buoi 6 Readiness - Team Core Business

## Published service

- Service: `core-business`
- Host port: `8000`
- Health: `GET http://<DEMO_IP>:8000/health`
- Authentication: `Authorization: Bearer <AUTH_TOKEN>`
- Contract: `contracts/core-business.openapi.yaml`

The API listens on `0.0.0.0`. On the classroom hotspot, replace
`<DEMO_IP>` with the current Wi-Fi IPv4 address and allow inbound TCP 8000 in
Windows Firewall.

## Integration endpoints

| Consumer | Method and path/topic | Success |
|---|---|---|
| IoT Ingestion | MQTT publish `smart-campus/events/sensor` | Core logs event in `/mqtt/events` |
| IoT Ingestion fallback | `POST /api/v1/sensor-events` | `202` |
| AI Vision | `POST /api/v1/detections` | `202` |
| Access Gate | `POST /api/v1/access-events` | `200` |

Core calls these providers:

| Provider | Environment variable | Path |
|---|---|---|
| Notification | `NOTIFICATION_SERVICE_URL` | `POST /api/v1/notifications` |
| Analytics | `ANALYTICS_SERVICE_URL` | `POST /api/v1/events` |

At home both variables point to `partner-service:9100`. In class, edit `.env`
to use the partners' hotspot IP addresses, for example:

```env
NOTIFICATION_SERVICE_URL=http://192.168.43.57:8000
ANALYTICS_SERVICE_URL=http://192.168.43.58:8000
PARTNER_TIMEOUT_SECONDS=3
PARTNER_RETRY_COUNT=0
```

Team IoT publishes to HiveMQ Cloud. In class, set MQTT variables like this
and fill username/password from the private group chat:

```env
MQTT_ENABLED=true
MQTT_HOST=f6f78e87db4a4c189dd3d706745a5e93.s1.eu.hivemq.cloud
MQTT_PORT=8883
MQTT_TLS=true
MQTT_USERNAME=<hivemq-username>
MQTT_PASSWORD=<hivemq-password>
MQTT_TOPIC=smart-campus/events/sensor
MQTT_QOS=1
```

Never put classroom IP addresses directly in Python source code.

## Failure behavior

- Partner calls use a configurable 3-second timeout.
- Timeout, connection failure, or partner 4xx/5xx returns RFC 7807 Problem
  Details with HTTP `503`.
- Policy decisions are persisted before outbound delivery, so a failed partner
  does not lose the Core decision.
- Consumers should retry with the same `Idempotency-Key`.
- Core `/health` checks its internal database and audit sink only.
- Core `/partners/health` reports HTTP partners and MQTT subscription state
  with `ok=false` on errors instead of hanging.
- MQTT messages are processed asynchronously, so IoT expects Core evidence in
  `/mqtt/events`, not an HTTP response body.

## Home verification

```bash
cp .env.example .env
docker compose up -d --build --wait
npm run lint:openapi
npm run test:all
```

Expected results:

- Lab 05: 12 requests, 35 assertions, 0 failures.
- Buoi 6: 9 requests, 23 assertions, 0 failures.
- MQTT smoke test: QoS 1 publish is recorded by Core and fan-out reaches the
  local Notification/Analytics mock.
- Timeout scenario: Analytics delays 5 seconds; Core returns `503` after about
  3 seconds and remains healthy afterward.

## Classroom sequence

1. Connect all Product laptops to the same hotspot.
2. Run `ipconfig` and publish the demo laptop Wi-Fi IPv4 and port 8000.
3. Update Notification, Analytics, and HiveMQ MQTT variables in `.env`.
4. Run `docker compose up -d --build --wait`.
5. From a second laptop, call `GET http://<DEMO_IP>:8000/health`.
6. Ask IoT to publish one message to `smart-campus/events/sensor`, then check
   `GET /mqtt/events`.
7. Run one agreed REST request for Vision and Gate.
8. Save partner health, MQTT event log, and request/response evidence in `reports/`.
