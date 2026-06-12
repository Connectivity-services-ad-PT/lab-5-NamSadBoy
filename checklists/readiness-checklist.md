# Readiness Checklist - Lab 05 Team Core

- [x] **Database ready:** PostgreSQL is healthy and `pg_isready -U lab05 -d coredb` passes.
- [x] **Audit service ready:** `GET :9000/health` returns 200 and accepted events are visible at `/events`.
- [x] **API ready:** `GET :8000/health` returns 200 only when DB and audit service are reachable.
- [x] **End-to-end policy flow:** access, sensor, and detection decisions are persisted; denied/critical outcomes create alerts.
- [x] **Environment variables:** API token, ports, service version, DB credentials and dependency URLs are externalized.
- [x] **Network and dependency order:** all services use `team-core-internal`; API waits for healthy DB and audit service.
- [x] **Non-root images:** API and audit service run with `appuser`.
- [x] **Image tags:** Core API and audit service use `v0.1.0-team-core`.
- [x] **Automated tests:** Newman report contains 12 requests and 35 passing assertions.
- [x] **Evidence:** health, DB counts, audit events, Compose status/logs and reports are stored in `reports/`.

Known limitation: registry publication depends on the linked registry credentials;
the reproducible local tags and GitHub Actions build remain available.
