# Run Lab 05 Core Business Stack

## Architecture

```text
Access Gate / Newman
        |
        v
Core Business API :8000
   |          |               |                  ^
   v          v               v                  |
PostgreSQL  Audit :9000   Partner mock :9100    |
decisions   internal log  Notification + Analytics
alerts                                             |
   ^                                               |
   | MQTT QoS 1 smart-campus/events/sensor         |
Mosquitto local / HiveMQ Cloud --------------------+
```

All containers communicate on `team-core-internal`.

## Start

```bash
cp .env.example .env
npm install
npm run lint:openapi
docker compose up -d --build --wait
docker compose ps
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
npm install
npm run lint:openapi
docker compose up -d --build --wait
docker compose ps
```

## Readiness checks

```bash
curl http://localhost:8000/health
curl http://localhost:9000/health
curl http://localhost:9100/health
docker compose exec -T db pg_isready -U lab05 -d coredb
```

Expected containers:

- `fit4110-core-api-lab05`
- `fit4110-core-audit-lab05`
- `fit4110-partner-mock-lab06`
- `fit4110-mqtt-broker-lab06`
- `fit4110-db-lab05`

## End-to-end test

```bash
npm run test:compose
curl http://localhost:9000/events
docker compose exec -T db psql -U lab05 -d coredb \
  -c "SELECT COUNT(*) FROM decisions; SELECT COUNT(*) FROM alerts;"
```

Expected Newman results:

- Lab 05: 12 requests, 35 assertions, 0 failures.
- Buoi 6: 9 requests, 23 assertions, 0 failures.
- MQTT: QoS 1 publish is received by Core and visible in `/mqtt/events`.

## Configuration

- API token comes from `AUTH_TOKEN`.
- Database credentials come from `POSTGRES_*`.
- API reaches PostgreSQL through hostname `db`.
- API reaches the audit integration through `http://audit-service:9000`.
- API reads `NOTIFICATION_SERVICE_URL` and `ANALYTICS_SERVICE_URL` from `.env`.
- At home both partner URLs point to `http://partner-service:9100`.
- In class replace them with the partner laptops' hotspot URLs.
- Partner calls are bounded by `PARTNER_TIMEOUT_SECONDS` (default 3 seconds).
- Local MQTT uses `mqtt-broker:1883`. For HiveMQ Cloud set
  `MQTT_HOST`, `MQTT_PORT=8883`, `MQTT_TLS=true`, `MQTT_USERNAME`, and
  `MQTT_PASSWORD` in `.env`.
- `.env` is local-only; only `.env.example` is committed.

## Image tags

- `fit4110/core-business:v0.1.0-team-core`
- `fit4110/core-audit:v0.1.0-team-core`
- `postgres:15-alpine`

## Optional class network

For plug-a-thon, create or join the lecturer-provided `class-net`, then attach
the API without changing the internal dependency network:

```bash
docker network connect class-net fit4110-core-api-lab05
```

## Stop

```bash
docker compose down -v
```
