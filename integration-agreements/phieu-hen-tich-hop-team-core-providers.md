# PHIẾU HẸN TÍCH HỢP - TEAM CORE GỌI PROVIDER

Tài liệu này dùng để gửi cho các nhóm provider mà `team-core` cần gọi theo
dependency map Smart Campus Operations Platform.

Thông tin chung của nhóm gọi:

- Nhóm gọi (consumer): `team-core`
- Service: `Core Business`
- Health của Core: `GET http://<CORE_DEMO_IP>:8000/health`
- Partner health của Core: `GET http://<CORE_DEMO_IP>:8000/partners/health`
- Timeout đề xuất: `3 giây`
- Correlation header cho REST: `X-Correlation-Id: <uuid>`

Khi test từ xa bằng Radmin VPN:

- REST provider URL dùng `http://<RADMIN_IP_PROVIDER>:<PORT>`.
- Core URL để nhóm khác gọi dùng `http://<RADMIN_IP_TEAM_CORE>:8000`.
- Không dùng `localhost`, IP Wi-Fi, hotspot IP, hoặc Docker service name khi
  gọi giữa hai máy demo khác nhau.
- MQTT vẫn đi qua broker cloud như HiveMQ, Radmin VPN không thay thế broker.

Provider cần phản hồi lại cho team-core:

- URL demo hoặc broker URL thực tế
- Port
- Auth token / username / password nếu có
- Health endpoint nếu là REST
- Topic queue nếu là async
- Một response/log mẫu để đối chiếu lúc tích hợp

---

## 1. Core Business gọi AI Vision

Nhóm gọi (consumer): `team-core`

Nhóm được gọi (provider): `team-ai-vision`

URL provider:

```text
http://<RADMIN_IP_TEAM_AI_VISION>:<PORT>
```

Endpoint sẽ gọi:

```text
METHOD: POST
PATH:   /api/v1/detect
```

Mục đích:

```text
Core lấy kết quả phân tích ảnh / detect từ AI Vision để kiểm tra policy an ninh.
```

Request mẫu:

```json
{
  "requestId": "vision-request-001",
  "sourceService": "team-core",
  "cameraId": "CAMERA-B6-01",
  "frameId": "frame-20260617-001",
  "frameUrl": "http://<RADMIN_IP_TEAM_CORE>:8000/evidence/frames/frame-20260617-001.jpg",
  "capturedAt": "2026-06-17T14:30:10+07:00",
  "reason": "security_policy_verification"
}
```

Response mong đợi:

```json
{
  "requestId": "vision-request-001",
  "detectionId": "detect-20260617-001",
  "status": "completed",
  "cameraId": "CAMERA-B6-01",
  "label": "UNKNOWN_PERSON",
  "confidence": 0.96,
  "boundingBoxes": [
    {
      "label": "person",
      "confidence": 0.96,
      "x": 120,
      "y": 80,
      "width": 220,
      "height": 360
    }
  ],
  "occurredAt": "2026-06-17T14:30:11+07:00",
  "processingMs": 184
}
```

Nếu provider lỗi hoặc timeout, nhóm consumer sẽ xử lý như sau:

```text
Core chờ tối đa 3 giây. Nếu AI Vision lỗi, timeout hoặc trả non-2xx,
Core ghi log VISION_PROVIDER_UNAVAILABLE kèm correlationId và không treo API.
Lần test lại dùng cùng requestId để đối chiếu log.
```

Đã test `GET http://<RADMIN_IP_TEAM_AI_VISION>:<PORT>/health`: `[ ]` Rồi   `[ ]` Chưa

---

## 2. Core Business gọi Access Gate

Nhóm gọi (consumer): `team-core`

Nhóm được gọi (provider): `team-access-gate`

URL provider:

```text
http://26.150.185.206:8000
```

Endpoint sẽ gọi:

```text
METHOD: GET
PATH:   /access-events?limit=10
```

Mục đích:

```text
Core lấy log quẹt thẻ / thông tin subject từ Access Gate để kiểm tra quyền ra vào.
```

Request mẫu:

```json
curl -X GET "http://26.150.185.206:8000/access-events?limit=10" -H "Authorization: Bearer local-dev-token"
```

Response mong đợi:

```json
{
  "items": [
    {
      "event_id": "EVT-20260618-0001",
      "card_id": "CARD-04A1B2C3D406",
      "gate_id": "GATE-01",
      "direction": "in",
      "result": "accepted",
      "deny_reason": null,
      "zone_id": "ZONE-A",
      "timestamp": "2026-06-18T09:30:00Z",
      "created_at": "2026-06-18T09:30:01Z"
    }
  ],
  "total": 1
}
```

Nếu provider lỗi hoặc timeout, nhóm consumer sẽ xử lý như sau:

```text
Core chờ tối đa 3 giây. Nếu Access Gate lỗi, timeout hoặc trả non-2xx,
Core ghi log ACCESS_GATE_PROVIDER_UNAVAILABLE và trả trạng thái partner ok=false
trong /partners/health, không làm treo /health.
```

Đã test `GET http://26.150.185.206:8000/health`: `[ ]` Rồi   `[ ]` Chưa

---

## 3. Core Business gửi Notification

Nhóm gọi (consumer): `team-core`

Nhóm được gọi (provider): `team-notification`

URL provider:

```text
mqtts://<BROKER_HOST>:8883
```

Endpoint/topic sẽ gọi:

```text
METHOD: MQTT PUBLISH
PATH:   smart-campus/alerts/core
QoS:    1
```

Mục đích:

```text
Core trigger gửi alert đa kênh khi policy tạo cảnh báo.
```

Request mẫu:

```json
{
  "notificationId": "notification-001",
  "eventType": "core.alert.created",
  "sourceService": "team-core",
  "timestamp": "2026-06-17T14:30:12+07:00",
  "correlationId": "corr-core-001",
  "alertId": "alert-001",
  "severity": "HIGH",
  "channel": "MULTI",
  "recipientGroup": "security-ops",
  "title": "Core policy alert: sensor",
  "message": "SENSOR_THRESHOLD_CRITICAL",
  "metadata": {
    "decisionId": "decision-001",
    "location": "Lab A101",
    "sourceEventId": "sensor-event-001"
  }
}
```

Response mong đợi:

```json
{
  "type": "async",
  "expectation": "team-notification subscribe topic and logs/sends received alert"
}
```

Nếu provider lỗi hoặc timeout, nhóm consumer sẽ xử lý như sau:

```text
Core publish QoS 1 và coi là queued khi broker ack. Nếu broker lỗi,
Core ghi notification_queue_unavailable kèm correlationId. Policy decision
vẫn được lưu, không mất quyết định Core.
```

Đã test broker/topic qua HiveMQ/broker cloud: `[ ]` Rồi   `[ ]` Chưa

---

## 4. Core Business gửi Analytics

Nhóm gọi (consumer): `team-core`

Nhóm được gọi (provider): `team-analytics`

URL provider:

```text
mqtts://<BROKER_HOST>:8883
```

Endpoint/topic sẽ gọi:

```text
METHOD: MQTT PUBLISH
PATH:   smart-campus/events/core
QoS:    1
```

Mục đích:

```text
Core feed event alert / policy cho KPI và dashboard Analytics.
```

Request mẫu:

```json
{
  "eventId": "core-event-001",
  "eventType": "core.sensor.processed",
  "sourceService": "team-core",
  "timestamp": "2026-06-17T14:30:12+07:00",
  "correlationId": "corr-core-001",
  "payload": {
    "input": {
      "eventId": "sensor-event-001",
      "deviceId": "SENSOR-ESP32-LAB-A101",
      "location": "Lab A101",
      "metric": "TEMPERATURE",
      "value": 42.1,
      "unit": "CELSIUS"
    },
    "result": {
      "decisionId": "decision-001",
      "outcome": "ALERT",
      "reasonCode": "SENSOR_THRESHOLD_CRITICAL",
      "alertId": "alert-001"
    }
  }
}
```

Response mong đợi:

```json
{
  "type": "async",
  "expectation": "team-analytics subscribe topic and stores event for aggregate/KPI"
}
```

Nếu provider lỗi hoặc timeout, nhóm consumer sẽ xử lý như sau:

```text
Core publish QoS 1. Nếu Analytics chưa nhận được, Core vẫn log
analytics_event_queued khi broker ack; Analytics kiểm tra lại subscription,
topic và credential. Nếu broker không kết nối được, Core ghi
analytics_queue_unavailable và /partners/health trả ok=false.
```

Đã test broker/topic qua HiveMQ/broker cloud: `[ ]` Rồi   `[ ]` Chưa
