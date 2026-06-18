# Báo cáo tích hợp Team Core - Smart Campus Operations Platform

## 1. Vai trò của nhóm

Nhóm thực hiện service **Core Business** trong hệ thống Smart Campus Operations Platform.

Core Business là service trung tâm xử lý nghiệp vụ và policy:

- Nhận dữ liệu từ các service khác như IoT, Access Gate, AI Vision.
- Kiểm tra, chuẩn hóa và áp dụng rule nghiệp vụ.
- Sinh kết quả policy như `ALLOW`, `DENY`, `ALERT`, `WARNING`.
- Gửi kết quả hoặc alert sang Analytics, Notification và các provider liên quan.

Trong dependency map, Team Core vừa là **provider** vừa là **consumer**:

| Vai trò | Team liên quan | Cơ chế | Trạng thái |
|---|---|---|---|
| Provider | IoT -> Core | MQTT `smart-campus/events/sensor` | Đã kết nối |
| Provider | Access Gate -> Core | REST `POST /api/v1/access-events` | Đã kết nối |
| Provider | AI Vision -> Core | REST `POST /api/v1/vision-results` | Đã kết nối |
| Consumer | Core -> Notification | REST `POST /events/alert.created` | Đã kết nối |
| Consumer | Core -> Analytics | REST `POST /events/core` | Đã kết nối |
| Consumer | Core -> Access Gate | REST `GET /access-events?limit=10` | Đã kết nối |

Thông tin service Core:

```text
Service: Core Business
Radmin IP: 26.183.48.228
Port: 8000
Base URL: http://26.183.48.228:8000
Auth header: Authorization: Bearer lab-core-token
Health: GET /health
Partner health: GET /partners/health
```

## 2. Input

Core Business nhận nhiều loại dữ liệu từ các service khác nhau.

### 2.1. IoT gửi sensor event qua MQTT

```text
Topic: smart-campus/events/sensor
QoS: 1
Source: team-iot
```

Payload mẫu:

```json
{
  "eventId": "sensor-event-001",
  "eventType": "sensor.reading.processed",
  "sourceService": "team-iot",
  "deviceId": "esp32-lab-a101",
  "location": "Lab A101",
  "temperatureC": 42.1,
  "humidityPercent": 71.2,
  "motionDetected": true,
  "co2Ppm": 710,
  "smokePpm": 0.03,
  "batteryPercent": 86,
  "status": "danger",
  "alertLevel": "high",
  "reason": "temperature_too_high"
}
```

### 2.2. Access Gate gửi request kiểm tra quyền ra/vào

```text
Method: POST
URL: http://26.183.48.228:8000/api/v1/access-events
Auth: Authorization: Bearer lab-core-token
```

Payload mẫu:

```json
{
  "requestId": "2f019138-fbd6-4f37-8c81-73b9e3500001",
  "cardId": "CARD-060001",
  "gateId": "GATE-A1",
  "direction": "ENTRY",
  "occurredAt": "2026-06-18T10:02:00+07:00",
  "subject": {
    "subjectId": "EMP-0601",
    "role": "STAFF",
    "cardStatus": "ACTIVE",
    "zone": "ADMIN"
  }
}
```

### 2.3. AI Vision gửi kết quả phân tích ảnh

```text
Method: POST
URL: http://26.183.48.228:8000/api/v1/vision-results
Auth: Authorization: Bearer lab-core-token
```

Payload mẫu:

```json
{
  "request_id": "vision-req-001",
  "camera_id": "cam-01",
  "location": "Gate A",
  "analysis": {
    "confidence": 0.96,
    "timestamp": "2026-06-18T10:00:00+07:00"
  },
  "labels": ["unknown_person"],
  "risk_level": "high",
  "summary": "Unknown person detected at Gate A"
}
```

### 2.4. Core gọi Access Gate để lấy log quẹt thẻ

Core cung cấp endpoint test nội bộ:

```text
Method: POST
URL: http://26.183.48.228:8000/api/v1/access-gate/log-query
Auth: Authorization: Bearer lab-core-token
```

Sau đó Core forward sang Access Gate:

```text
Provider: http://26.150.185.206:8000
Method: GET
Path: /access-events?limit=10
Auth: Authorization: Bearer local-dev-token
```

## 3. Xử lý nghiệp vụ

Core xử lý input theo các bước chính:

1. **Validate dữ liệu đầu vào**

   - Kiểm tra UUID của `requestId`.
   - Kiểm tra format `cardId`, `gateId`.
   - Kiểm tra enum như `direction`, `role`, `cardStatus`, `status`.
   - Kiểm tra JSON body và auth token.

2. **Chuẩn hóa dữ liệu**

   - Hỗ trợ payload camelCase từ IoT.
   - Hỗ trợ callback snake_case từ AI Vision.
   - Chuyển dữ liệu về model nội bộ để xử lý policy.

3. **Áp dụng rule nghiệp vụ**

   Với Access Gate:

   - `cardStatus = ACTIVE` và role hợp lệ -> `ALLOW`.
   - Thẻ bị khóa hoặc không hợp lệ -> `DENY`.
   - Trả kèm `decisionId`, `policyId`, `reasonCode`, `expiresAt`.

   Với IoT:

   - `status = danger` -> tạo alert.
   - `status = warning` -> tạo warning alert.
   - `reason = smoke_detected` -> tạo alert khẩn.
   - `motionDetected = true` ngoài giờ -> tạo alert bất thường.

   Với AI Vision:

   - `risk_level = high` hoặc label nguy hiểm -> tạo alert.
   - Kết quả detect được đưa vào policy decision.

4. **Fan-out kết quả**

   - Event xử lý xong được gửi sang Analytics.
   - Nếu có alert thì gửi sang Notification.
   - Kết quả policy trả trực tiếp về consumer nếu là REST sync.

5. **Chống treo khi provider lỗi**

   - Timeout partner mặc định 3 giây.
   - Nếu provider lỗi hoặc timeout, Core trả lỗi rõ ràng và `/health` không bị treo.
   - `/partners/health` dùng để kiểm tra trạng thái các service phụ thuộc.

## 4. Output

Core trả output tùy loại input.

### 4.1. Output cho Access Gate

Response mẫu khi Access Gate hỏi quyền ra/vào:

```json
{
  "eventType": "access-event",
  "status": "processed",
  "correlationId": "a7fc6f0f-8687-4bc0-95f0-df55b26df0a4",
  "result": {
    "decisionId": "8851ced0-5d38-45b6-80dd-073d2bdf09da",
    "requestId": "2f019138-fbd6-4f37-8c81-73b9e3500001",
    "decision": "ALLOW",
    "reasonCode": "POLICY_ALLOW",
    "policyId": "7e34be8b-1da8-483e-b5ae-28f8662d0ac7",
    "expiresAt": "2026-06-18T03:05:41.421597Z",
    "explanation": "Active staff card is allowed in ADMIN zone."
  }
}
```

### 4.2. Output khi Core gửi sang Analytics

Analytics nhận event:

```json
{
  "eventType": "core.access-event.processed",
  "source": "core-business",
  "correlationId": "a7fc6f0f-8687-4bc0-95f0-df55b26df0a4",
  "payload": {
    "input": "...",
    "result": "..."
  }
}
```

Kết quả test thực tế:

```text
analytics.status = accepted
analytics.statusCode = 200
providerResponse.success = true
providerResponse.message = Core event received successfully
eventId = c3c2f770-0a93-4111-8a19-5ccb35b7beb3
```

### 4.3. Output khi Core gửi sang Notification

Notification nhận alert:

```json
{
  "eventType": "core.alert.created",
  "sourceService": "team-core",
  "channel": "MULTI",
  "severity": "HIGH",
  "title": "Core policy alert",
  "message": "Policy alert generated.",
  "recipientGroup": "security-ops"
}
```

Kết quả test thực tế:

```text
notification.status = accepted
statusCode = 202
status = queued
```

### 4.4. Output khi Core lấy log từ Access Gate

Core gọi Access Gate và trả lại response:

```json
{
  "delivery": {
    "provider": "access-gate",
    "status": "accepted",
    "statusCode": 200,
    "method": "GET",
    "providerResponse": {
      "items": [
        {
          "event_id": "4580e5d2-a505-4489-8067-9079df872405",
          "card_id": "04:A1:B2:C3:D4:05",
          "gate_id": "GATE-01",
          "direction": "in",
          "result": "accepted"
        }
      ],
      "total": 10
    }
  }
}
```

## 5. Output gửi cho ai?

| Output | Bên nhận | Endpoint/topic | Cơ chế | Trạng thái |
|---|---|---|---|---|
| Policy decision ra/vào | Access Gate | Response của `POST /api/v1/access-events` | REST sync | Đã OK |
| Sensor policy/alert | Analytics | `POST http://26.22.249.37:8000/events/core` | REST sync | Đã OK |
| Alert đa kênh | Notification | `POST http://26.95.36.20:8000/events/alert.created` | REST sync | Đã OK |
| Vision alert/policy | Analytics, Notification | `/events/core`, `/events/alert.created` | REST sync | Đã OK |
| Log quẹt thẻ | Core nhận từ Access Gate | `GET http://26.150.185.206:8000/access-events` | REST sync | Đã OK |

Các biến môi trường live đang dùng:

```env
NOTIFICATION_SERVICE_URL=http://26.95.36.20:8000
NOTIFICATION_PATH=/events/alert.created
NOTIFICATION_AUTH_TOKEN=local-dev-token

ANALYTICS_SERVICE_URL=http://26.22.249.37:8000
ANALYTICS_PATH=/events/core
ANALYTICS_AUTH_TOKEN=local-dev-token

ACCESS_GATE_SERVICE_URL=http://26.150.185.206:8000
ACCESS_GATE_PATH=/access-events
ACCESS_GATE_AUTH_TOKEN=local-dev-token
ACCESS_GATE_METHOD=GET
```

## 6. Minh chứng demo

### 6.1. Chạy hệ thống

Lệnh chạy:

```powershell
cd "D:\Dịch vụ kết nối\lab-5-NamSadBoy"
docker compose up -d --build --wait
```

Kiểm tra container:

```powershell
docker compose ps
```

Các container chính đã healthy:

```text
fit4110-core-api-lab05       Healthy
fit4110-core-audit-lab05     Healthy
fit4110-db-lab05             Healthy
fit4110-mqtt-broker-lab06    Healthy
fit4110-partner-mock-lab06   Healthy
```

### 6.2. Kiểm tra health

```powershell
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8000/partners/health
```

Kết quả partner health:

```text
notification ok=true statusCode=200
analytics    ok=true statusCode=200
access-gate  ok=true statusCode=200
mqtt         ok=true
```

### 6.3. Minh chứng IoT -> Core

Evidence:

```text
reports/mqtt-iot-policy-cases.json
reports/mqtt-events.json
reports/mqtt-status.txt
```

Kết quả:

```text
Core subscribe topic smart-campus/events/sensor
Core nhận sensor event từ team-iot
Core xử lý status danger/warning/smoke/motion
Core tạo alert hoặc warning theo policy
```

### 6.4. Minh chứng Access Gate -> Core và Core -> Access Gate

Evidence:

```text
reports/access-gate-integration-test.json
```

Kết quả Core -> Gate:

```text
Provider: http://26.150.185.206:8000
Endpoint: GET /access-events?limit=10
StatusCode: 200
Method: GET
providerResponse.total = 10
```

### 6.5. Minh chứng Core -> Analytics

Evidence:

```text
reports/analytics-live-integration-test.json
```

Kết quả:

```text
Provider: http://26.22.249.37:8000
Endpoint: POST /events/core
StatusCode: 200
success = true
eventId = c3c2f770-0a93-4111-8a19-5ccb35b7beb3
Readback /events exactMatchCount = 1
```

### 6.6. Minh chứng Core -> Notification

Evidence:

```text
reports/notification-a7-test.json
```

Kết quả:

```text
Provider: http://26.95.36.20:8000
Endpoint: POST /events/alert.created
StatusCode: 202
status = queued
```

### 6.7. Minh chứng AI Vision -> Core

Evidence:

```text
reports/vision-integration-test.json
```

Endpoint Core cho AI Vision:

```text
POST http://26.183.48.228:8000/api/v1/vision-results
Authorization: Bearer lab-core-token
```

Kết quả:

```text
Core nhận vision result
Core xử lý detection policy
Core tạo alert khi risk_level high
Core forward kết quả sang Analytics/Notification khi cần
```

### 6.8. Test tự động và CI

Các kiểm tra đã chạy:

```powershell
python -m compileall -q src
npm run lint:openapi
docker compose --env-file .env config --quiet
```

GitHub Actions gần nhất:

```text
Commit: 62a5a99 docs: record analytics live integration
Workflow: Lab 05 and Buoi 06 Integration Check
Conclusion: success
```

## Kết luận

Team Core đã hoàn thành tích hợp chính với các service liên quan:

- IoT -> Core: đã nhận MQTT sensor event.
- Access Gate -> Core: đã xử lý kiểm tra quyền ra/vào realtime.
- Core -> Access Gate: đã lấy log quẹt thẻ thành công.
- AI Vision -> Core: đã nhận kết quả phân tích ảnh.
- Core -> Notification: đã gửi alert thành công.
- Core -> Analytics: đã gửi event KPI/policy thành công và đọc lại được trên `/events`.

Như vậy service Core Business đã đáp ứng vai trò trung tâm xử lý policy, alert và fan-out event trong Smart Campus Operations Platform.
