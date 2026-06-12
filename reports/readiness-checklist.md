# Buoi 6 Evidence Checklist

- [x] Compose stack builds from a clean local volume.
- [x] Core, audit, partner mock, and PostgreSQL become healthy.
- [x] Core listens on host port 8000 and binds `0.0.0.0`.
- [x] `GET /health` returns HTTP 200 locally and through the LAN IPv4.
- [x] IoT, Vision, and Gate integration endpoints pass automated tests.
- [x] Notification and Analytics fan-out is verified by the partner mock.
- [x] A 5-second partner delay is cut off by Core at about 3 seconds.
- [x] Partner timeout returns HTTP 503 Problem Details.
- [x] Core remains healthy after the partner timeout test.
- [x] OpenAPI lint and both Newman suites pass.
- [ ] A second classroom laptop can call Core through the shared hotspot.
- [ ] Core can call the real Notification and Analytics team laptops.
- [ ] Windows Firewall inbound rule for TCP 8000 is confirmed on the demo laptop.
