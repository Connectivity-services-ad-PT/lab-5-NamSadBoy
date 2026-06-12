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

| Consumer | Method and path | Success |
|---|---|---|
| IoT Ingestion | `POST /api/v1/sensor-events` | `202` |
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

Never put classroom IP addresses directly in Python source code.

## Failure behavior

- Partner calls use a configurable 3-second timeout.
- Timeout, connection failure, or partner 4xx/5xx returns RFC 7807 Problem
  Details with HTTP `503`.
- Policy decisions are persisted before outbound delivery, so a failed partner
  does not lose the Core decision.
- Consumers should retry with the same `Idempotency-Key`.
- Core `/health` checks its internal database and audit sink only.

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
- Timeout scenario: Analytics delays 5 seconds; Core returns `503` after about
  3 seconds and remains healthy afterward.

## Classroom sequence

1. Connect all Product laptops to the same hotspot.
2. Run `ipconfig` and publish the demo laptop Wi-Fi IPv4 and port 8000.
3. Update Notification and Analytics URLs in `.env`.
4. Run `docker compose up -d --build --wait`.
5. From a second laptop, call `GET http://<DEMO_IP>:8000/health`.
6. Run one agreed request for IoT, Vision, and Gate.
7. Save partner health and request/response screenshots in `reports/`.

