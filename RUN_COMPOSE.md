# Run Lab 05 Core Business Stack

## Architecture

```text
Access Gate / Newman
        |
        v
Core Business API :8000
   |             |
   v             v
PostgreSQL     Audit service :9000
decisions      policy.decision.created
alerts         alert.created
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
docker compose exec -T db pg_isready -U lab05 -d coredb
```

Expected containers:

- `fit4110-core-api-lab05`
- `fit4110-core-audit-lab05`
- `fit4110-db-lab05`

## End-to-end test

```bash
npm run test:compose
curl http://localhost:9000/events
docker compose exec -T db psql -U lab05 -d coredb \
  -c "SELECT COUNT(*) FROM decisions; SELECT COUNT(*) FROM alerts;"
```

Expected Newman result: 12 requests, 35 assertions, 0 failures.

## Configuration

- API token comes from `AUTH_TOKEN`.
- Database credentials come from `POSTGRES_*`.
- API reaches PostgreSQL through hostname `db`.
- API reaches the audit integration through `http://audit-service:9000`.
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
