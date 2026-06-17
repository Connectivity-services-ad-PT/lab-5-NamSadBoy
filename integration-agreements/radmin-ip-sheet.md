# Radmin IP Sheet - Buổi 6 Integration

Tài liệu này dùng để ghi IP chung khi các nhóm test tích hợp từ xa qua Radmin
VPN. REST gọi qua Radmin IP; MQTT vẫn đi qua broker cloud như HiveMQ.

## Quy tắc dùng IP

- Mỗi nhóm chọn một máy demo chính để chạy `docker compose up -d --build`.
- Máy demo chính join cùng Radmin Network với các nhóm cần tích hợp.
- Nhóm khác gọi REST bằng `http://<RADMIN_IP_PROVIDER>:<PORT>`.
- Không dùng `localhost` của máy khác.
- Không dùng Docker service name như `api`, `partner-service`, `mqtt-broker`
  khi gọi từ máy nhóm khác.
- Service phải bind `0.0.0.0` và publish port ra host, ví dụ `8000:8000`.
- Mở Windows Firewall cho port demo nếu máy khác gọi bị timeout.

## Bảng IP chung

| Nhóm | Service | Radmin IP | Port REST | Health URL | Ghi chú |
|---|---|---:|---:|---|---|
| team-core | Core Business | `26.__.__.__` | `8000` | `http://26.__.__.__:8000/health` | Provider cho IoT/Vision/Gate |
| team-iot | IoT Ingestion | `26.__.__.__` | `8000` | `http://26.__.__.__:8000/health` | MQTT publish sang Core qua HiveMQ |
| team-camera | Camera Stream | `26.__.__.__` | `8000` | `http://26.__.__.__:8000/health` | Gọi AI Vision |
| team-ai-vision | AI Vision | `26.__.__.__` | `8000` | `http://26.__.__.__:8000/health` | Provider REST cho Core |
| team-access-gate | Access Gate | `26.__.__.__` | `8000` | `http://26.__.__.__:8000/health` | Provider REST cho Core |
| team-notification | Notification | `26.__.__.__` | `8000` | `http://26.__.__.__:8000/health` | Có thể REST hoặc MQTT tùy contract |
| team-analytics | Analytics | `26.__.__.__` | `8000` | `http://26.__.__.__:8000/health` | Có thể REST hoặc MQTT tùy contract |

## Cấu hình `.env` của team-core

Nếu provider dùng REST qua Radmin:

```env
NOTIFICATION_SERVICE_URL=http://<RADMIN_IP_TEAM_NOTIFICATION>:8000
ANALYTICS_SERVICE_URL=http://<RADMIN_IP_TEAM_ANALYTICS>:8000
PARTNER_TIMEOUT_SECONDS=3
PARTNER_RETRY_COUNT=0
```

Nếu provider dùng MQTT qua HiveMQ, Radmin IP không thay thế broker:

```env
MQTT_ENABLED=true
MQTT_HOST=<HIVEMQ_HOST>
MQTT_PORT=8883
MQTT_TLS=true
MQTT_USERNAME=<hivemq-username>
MQTT_PASSWORD=<hivemq-password>
MQTT_TOPIC=smart-campus/events/sensor
MQTT_QOS=1
```

## Test bắt buộc

Provider tự kiểm tra trên máy mình:

```powershell
docker compose ps
curl http://localhost:8000/health
```

Consumer gọi sang provider bằng Radmin IP:

```powershell
curl http://<RADMIN_IP_PROVIDER>:8000/health
```

Team-core kiểm tra các partner:

```powershell
curl http://localhost:8000/partners/health
curl http://<RADMIN_IP_TEAM_CORE>:8000/partners/health
```

Nếu local `/health` OK nhưng máy khác timeout, kiểm tra theo thứ tự:

1. Service đã publish port ra host chưa.
2. Service có bind `0.0.0.0` không.
3. Windows Firewall đã mở port chưa.
4. Hai máy có cùng Radmin Network không.
5. URL trong `.env` có dùng đúng Radmin IP không.

