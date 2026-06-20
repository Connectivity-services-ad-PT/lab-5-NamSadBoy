import mqtt from "mqtt";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import { randomUUID } from "node:crypto";


const baseUrl = process.env.CORE_BASE_URL || "http://localhost:8000";
const brokerUrl = process.env.MQTT_TEST_URL || "mqtt://localhost:1883";
const authToken = process.env.AUTH_TOKEN || "lab-core-token";
const reportPath = process.env.BUSINESS_REPORT_PATH || "reports/core-business-scenarios.json";

async function api(path, { method = "GET", body, idempotencyKey } = {}) {
  const response = await fetch(`${baseUrl}${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${authToken}`,
      "Content-Type": "application/json",
      "X-Correlation-Id": randomUUID(),
      ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {})
    },
    body: body ? JSON.stringify(body) : undefined
  });
  const responseBody = await response.json();
  if (!response.ok) {
    throw new Error(`${method} ${path} failed with ${response.status}: ${JSON.stringify(responseBody)}`);
  }
  return responseBody;
}

async function publish(topic, payload) {
  const client = mqtt.connect(brokerUrl, {
    clean: true,
    connectTimeout: 5000,
    reconnectPeriod: 0
  });
  await new Promise((resolve, reject) => {
    client.once("connect", resolve);
    client.once("error", reject);
  });
  await new Promise((resolve, reject) => {
    client.publish(topic, JSON.stringify(payload), { qos: 1 }, (error) => {
      if (error) reject(error);
      else resolve();
    });
  });
  client.end(true);
}

async function waitForMqttEvent(eventId) {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    const events = await api("/mqtt/events?limit=100");
    const match = events.items.find((item) => item.mqttEventId === eventId);
    if (match) return match;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`Core did not process MQTT event ${eventId}`);
}

const runId = Date.now();
const now = new Date();

const smokeEventId = `sensor-business-${runId}`;
await publish("smart-campus/events/sensor", {
  eventId: smokeEventId,
  eventType: "sensor.reading.processed",
  sourceService: "a1-iot-ingestion",
  timestamp: now.toISOString(),
  rawEventId: `raw-iot-business-${runId}`,
  deviceId: `esp32-business-${runId}`,
  location: `Lab Business ${runId}`,
  temperatureC: 31.5,
  humidityPercent: 70,
  motionDetected: false,
  lightLux: 400,
  co2Ppm: 700,
  smokePpm: 1.2,
  batteryPercent: 80,
  status: "danger",
  alertLevel: "critical",
  reason: "smoke_detected"
});
const smokeResult = await waitForMqttEvent(smokeEventId);
if (smokeResult.result.reasonCode !== "IOT_SMOKE_DETECTED_CRITICAL") {
  throw new Error(`Unexpected smoke policy: ${smokeResult.result.reasonCode}`);
}

const intrusionCard = `CARD-${runId}`;
const intrusionAccess = await api("/policies/evaluate-access", {
  method: "POST",
  idempotencyKey: `business-intrusion-access-${runId}`,
  body: {
    requestId: randomUUID(),
    cardId: intrusionCard,
    gateId: "GATE-01",
    direction: "ENTRY",
    occurredAt: now.toISOString(),
    subject: {
      subjectId: `SUBJECT-${runId}`,
      role: "STAFF",
      cardStatus: "REVOKED",
      zone: "ADMIN"
    }
  }
});
const intrusionVision = await api("/policies/evaluate-detection", {
  method: "POST",
  idempotencyKey: `business-intrusion-vision-${runId}`,
  body: {
    requestId: randomUUID(),
    detectionId: randomUUID(),
    cameraId: "CAMERA-GATE-01",
    label: "UNKNOWN_PERSON",
    confidence: 0.95,
    occurredAt: new Date(now.getTime() + 1000).toISOString(),
    location: "Main Gate A"
  }
});
if (intrusionVision.reasonCode !== "INTRUSION_CORRELATED") {
  throw new Error(`Unexpected intrusion policy: ${intrusionVision.reasonCode}`);
}

const repeatedCard = `CARD-R${runId}`;
const repeatedResults = [];
for (let index = 0; index < 3; index += 1) {
  repeatedResults.push(await api("/policies/evaluate-access", {
    method: "POST",
    idempotencyKey: `business-repeat-${runId}-${index}`,
    body: {
      requestId: randomUUID(),
      cardId: repeatedCard,
      gateId: "GATE-02",
      direction: "ENTRY",
      occurredAt: new Date(now.getTime() + 2000 + index * 1000).toISOString(),
      subject: {
        subjectId: `SUBJECT-R${runId}`,
        role: "STAFF",
        cardStatus: "REVOKED",
        zone: "ADMIN"
      }
    }
  }));
}
const repeatedFinal = repeatedResults.at(-1);
if (repeatedFinal.reasonCode !== "REPEATED_ACCESS_DENIED" || !repeatedFinal.alertId) {
  throw new Error(`Repeated access policy did not alert: ${JSON.stringify(repeatedFinal)}`);
}

const report = {
  testedAt: new Date().toISOString(),
  scenario1EnvironmentDanger: {
    status: "passed",
    eventId: smokeEventId,
    outcome: smokeResult.result.outcome,
    reasonCode: smokeResult.result.reasonCode,
    severity: smokeResult.result.severity,
    analyticsMqtt: smokeResult.deliveries.analyticsMqtt.status,
    notificationMqtt: smokeResult.deliveries.notificationMqtt.status
  },
  scenario2UnknownPersonAtGate: {
    status: "passed",
    accessDecision: intrusionAccess.decision,
    reasonCode: intrusionVision.reasonCode,
    severity: intrusionVision.severity,
    evidenceEventIds: intrusionVision.correlatedEvidenceEventIds
  },
  scenario3RepeatedDeniedAccess: {
    status: "passed",
    attempts: repeatedFinal.deniedAttemptsInWindow,
    reasonCode: repeatedFinal.reasonCode,
    severity: repeatedFinal.severity,
    alertId: repeatedFinal.alertId
  }
};

await mkdir(dirname(reportPath), { recursive: true });
await writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`);
console.log(JSON.stringify(report, null, 2));
