# Nghiệp vụ thật của Team Core trong hệ thống 7 service

## 1. Phạm vi của Core

Core không xử lý thay IoT, Gate, Camera, Vision, Notification hoặc Analytics. Core nhận các event đã được
service nguồn validate, normalize và classify, sau đó áp policy cấp hệ thống để tạo quyết định và cảnh báo.

Core nhận dữ liệu qua hai cơ chế đang được hỗ trợ song song:

- MQTT: `smart-campus/events/sensor`, `smart-campus/events/access`, `smart-campus/events/camera`.
- REST: callback Access Gate, AI Vision và các endpoint kiểm thử hiện có.

Core gửi kết quả sang Notification và Analytics bằng các endpoint đã thống nhất trong `.env`. MQTT và REST
được giữ song song trong giai đoạn tích hợp để không làm hỏng contract đang chạy của các nhóm.

## 2. Contract IoT A

Core chỉ xử lý sensor event có:

```text
sourceService = a1-iot-ingestion
```

Event từ nguồn khác được ghi nhận với trạng thái `ignored_source`, không chạy policy và không tạo alert.

| Trạng thái IoT | Quyết định Core | Severity | Reason code |
|---|---|---|---|
| `normal` | Không tạo alert | `LOW` | `SENSOR_NORMAL` |
| `warning` | Tạo warning alert | `MEDIUM` | `IOT_STATUS_WARNING` |
| `danger` | Tạo alert | `HIGH` hoặc `CRITICAL` | `IOT_STATUS_DANGER` |
| `sensor_error` | Tạo alert kỹ thuật | `HIGH` | `IOT_SENSOR_ERROR` |
| `invalid_device` | Tạo alert an toàn dữ liệu | `HIGH` | `IOT_INVALID_DEVICE` |
| `smoke_detected` | Tạo alert khẩn | `CRITICAL` | `IOT_SMOKE_DETECTED_CRITICAL` |

Motion ngoài 07:00-18:00 tạo alert `IOT_MOTION_DETECTED_OUT_OF_HOURS` mức `HIGH`.

## 3. Policy đa nguồn

### 3.1. Nghi dò thẻ

Một UID/card bị từ chối ít nhất 3 lần tại cùng cổng trong 5 phút tạo alert:

```text
reasonCode = REPEATED_ACCESS_DENIED
severity = MEDIUM
```

Một lần denied riêng lẻ vẫn được lưu audit nhưng không gây nhiễu Notification.

### 3.2. Nghi đột nhập

AI Vision phát hiện `UNKNOWN_PERSON` với confidence từ 0.8 và có access denied tại cùng khu vực trong vòng
2 phút tạo alert:

```text
reasonCode = INTRUSION_CORRELATED
severity = CRITICAL
```

Tên vị trí được chuẩn hóa để `Main Gate A`, `Gate A` và `GATE-01` cùng ánh xạ về `GATE-01`.

### 3.3. Camera ngoài giờ

Camera có motion ngoài giờ và không có access granted cùng khu vực trong cửa sổ 2 phút tạo alert:

```text
reasonCode = CAMERA_MOTION_OUTSIDE_HOURS_NO_VALID_ACCESS
severity = HIGH
```

### 3.4. Môi trường nguy hiểm

Khói, nhiệt độ hoặc trạng thái danger tạo alert ngay, không cần chờ nguồn khác. Normal chỉ lưu fact và audit.

## 4. Chống trùng và truy vết

- Event đã xử lý được khóa bằng idempotency key trong PostgreSQL.
- Alert dùng `dedupKey`; cùng loại sự cố, vị trí và thiết bị không gửi lại trong 5 phút.
- Mỗi alert lưu `alertType`, `location`, `evidenceEventIds`, `severity` và `decisionId`.
- Mọi quyết định, alert và alert bị chặn do trùng đều được gửi sang Audit Service.

## 5. Ba kịch bản demo

1. IoT publish `smoke_detected` -> Core tạo `CRITICAL` -> Notification nhận đủ severity -> Analytics nhận decision.
2. Gate denied tại `GATE-01`, sau đó Vision báo người lạ tại `Main Gate A` -> Core tạo `INTRUSION_CORRELATED`.
3. Cùng card bị denied 3 lần trong 5 phút -> hai lần đầu chỉ audit, lần thứ ba tạo alert `MEDIUM`.

## 6. Lệnh kiểm thử

```powershell
docker compose up -d --build --wait
docker compose ps
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8000/mqtt/status
docker compose logs api --tail 100
```

Chạy test rule trong image Core:

```powershell
docker compose run --rm --no-deps `
  -v "${PWD}/tests:/app/tests:ro" `
  api python -m unittest discover -s tests -v
```
