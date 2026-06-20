import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from core_app import main as core  # noqa: E402


def make_iot_event(**overrides):
    payload = {
        "eventId": "sensor-event-test-001",
        "eventType": "sensor.reading.processed",
        "sourceService": "a1-iot-ingestion",
        "timestamp": "2026-06-20T14:45:33+07:00",
        "rawEventId": "raw-iot-test-001",
        "deviceId": "esp32-lab-a101",
        "location": "Lab A101",
        "temperatureC": 31.5,
        "humidityPercent": 70.0,
        "motionDetected": False,
        "lightLux": 438,
        "co2Ppm": 665,
        "smokePpm": 0.02,
        "batteryPercent": 63,
        "status": "normal",
        "alertLevel": "low",
        "reason": "environment_normal",
    }
    payload.update(overrides)
    return core.IotMqttSensorEvent.model_validate(payload)


class IotPolicyTests(unittest.TestCase):
    def classify(self, **overrides):
        event = make_iot_event(**overrides)
        request = core.iot_event_to_sensor_request(event)
        return core.classify_iot_sensor_event(event, request)

    def test_normal_does_not_alert(self):
        self.assertEqual(self.classify(), ("NORMAL", "SENSOR_NORMAL", "LOW"))

    def test_warning_creates_medium_decision(self):
        self.assertEqual(
            self.classify(status="warning", alertLevel="medium", reason="humidity_warning"),
            ("WARNING", "IOT_STATUS_WARNING", "MEDIUM"),
        )

    def test_danger_is_high(self):
        self.assertEqual(
            self.classify(
                status="danger",
                alertLevel="high",
                reason="temperature_too_high",
                temperatureC=42.1,
            ),
            ("ALERT", "IOT_STATUS_DANGER", "HIGH"),
        )

    def test_smoke_detected_is_critical(self):
        self.assertEqual(
            self.classify(
                status="danger",
                alertLevel="critical",
                reason="smoke_detected",
                smokePpm=1.2,
            ),
            ("ALERT", "IOT_SMOKE_DETECTED_CRITICAL", "CRITICAL"),
        )

    def test_sensor_error_and_invalid_device_are_explicit(self):
        self.assertEqual(
            self.classify(
                status="sensor_error",
                alertLevel="high",
                reason="missing_sensor_value",
                temperatureC=None,
            ),
            ("ALERT", "IOT_SENSOR_ERROR", "HIGH"),
        )
        self.assertEqual(
            self.classify(
                status="invalid_device",
                alertLevel="high",
                reason="device_not_registered",
            ),
            ("ALERT", "IOT_INVALID_DEVICE", "HIGH"),
        )

    def test_wrong_iot_source_is_ignored_before_policy(self):
        event = make_iot_event(sourceService="b1-iot-ingestion")
        core.mqtt_events.clear()
        core.process_iot_mqtt_message(
            core.MQTT_TOPIC,
            1,
            json.dumps(event.model_dump(mode="json")).encode(),
        )
        self.assertEqual(core.mqtt_events[-1]["status"], "ignored_source")


class CorrelationPolicyTests(unittest.TestCase):
    def test_gate_location_aliases_are_correlated(self):
        self.assertEqual(core.canonical_location("Main Gate A"), "GATE-01")
        self.assertEqual(core.canonical_location("GATE-01"), "GATE-01")

    def test_unknown_person_plus_recent_denial_is_critical(self):
        payload = core.DetectionEvaluationRequest(
            requestId=uuid4(),
            detectionId=uuid4(),
            cameraId="CAMERA-CAM-01",
            label=core.DetectionLabel.UNKNOWN_PERSON,
            confidence=0.95,
            occurredAt=datetime(2026, 6, 20, 15, 30, tzinfo=timezone.utc),
            location="Main Gate A",
        )
        denial_fact = {
            "sourceEventId": "gate-denied-001",
            "location": "GATE-01",
            "occurredAt": "2026-06-20T15:29:30Z",
            "payload": {"result": {"decision": "DENY"}},
        }
        with (
            patch.object(core, "cached", return_value=None),
            patch.object(core, "record_event_fact"),
            patch.object(core, "recent_event_facts", return_value=[denial_fact]),
            patch.object(core, "create_alert", return_value="alert-correlated-001"),
            patch.object(core, "persist_decision"),
            patch.object(core, "publish_audit_event"),
            patch.object(core, "remember"),
        ):
            result = core.evaluate_detection(payload, "vision-test-001")

        self.assertEqual(result["reasonCode"], "INTRUSION_CORRELATED")
        self.assertEqual(result["severity"], "CRITICAL")
        self.assertEqual(result["alertId"], "alert-correlated-001")
        self.assertEqual(result["correlatedEvidenceEventIds"], ["gate-denied-001"])


class DeliveryContractTests(unittest.TestCase):
    def test_notification_receives_decision_severity(self):
        source = core.SensorEvaluationRequest(
            requestId=uuid4(),
            deviceId="SENSOR-ESP32-A101",
            metric=core.SensorMetric.SMOKE,
            value=1.2,
            unit=core.SensorUnit.PPM,
            occurredAt=datetime.now(timezone.utc),
        )
        result = {
            "decisionId": str(uuid4()),
            "alertId": str(uuid4()),
            "reasonCode": "IOT_SMOKE_DETECTED_CRITICAL",
            "severity": "CRITICAL",
        }
        with patch.object(
            core,
            "deliver_to_partner",
            return_value={"status": "accepted"},
        ) as delivery:
            core.deliver_integration_result("sensor-event", source, result, str(uuid4()))

        notification_payload = delivery.call_args_list[1].args[3]
        self.assertEqual(notification_payload["severity"], "CRITICAL")
        self.assertEqual(
            notification_payload["channels"],
            ["telegram", "email", "app"],
        )


if __name__ == "__main__":
    unittest.main()
