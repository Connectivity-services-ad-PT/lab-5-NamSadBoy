import mqtt from "mqtt";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

const baseUrl = process.env.CORE_BASE_URL || "http://localhost:8000";
const brokerUrl = process.env.MQTT_TEST_URL || "mqtt://localhost:1883";
const topic = process.env.MQTT_TOPIC || "smart-campus/events/sensor";
const authToken = process.env.AUTH_TOKEN || "lab-core-token";
const reportPath = process.env.MQTT_REPORT_PATH || "reports/mqtt-smoke-test.json";
const eventId = `sensor-event-${Date.now()}`;

const payload = {
  eventId,
  eventType: "sensor.reading.processed",
  sourceService: "team-iot",
  deviceId: "esp32-lab-a101",
  location: "Lab A101",
  temperatureC: 42.1,
  humidityPercent: 71.2,
  motionDetected: true,
  lightLux: 390,
  co2Ppm: 710,
  smokePpm: 0.03,
  batteryPercent: 86,
  status: "danger",
  alertLevel: "high",
  reason: "temperature_too_high"
};

async function api(path, options = {}) {
  const response = await fetch(`${baseUrl}${path}`, {
    ...options,
    headers: {
      Authorization: `Bearer ${authToken}`,
      ...(options.headers || {})
    }
  });
  if (!response.ok) {
    throw new Error(`${options.method || "GET"} ${path} failed with ${response.status}`);
  }
  return response.json();
}

async function publish() {
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

async function waitForCoreEvent() {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    const events = await api("/mqtt/events?limit=20");
    const match = events.items.find((item) => item.mqttEventId === eventId);
    if (match) {
      if (match.status !== "processed") {
        throw new Error(`MQTT event status was ${match.status}`);
      }
      if (match.result?.outcome !== "ALERT") {
        throw new Error(`MQTT event outcome was ${match.result?.outcome}`);
      }
      if (match.deliveries?.analytics?.status !== "accepted") {
        throw new Error("Analytics did not accept MQTT fan-out");
      }
      if (match.deliveries?.notification?.status !== "accepted") {
        throw new Error("Notification did not accept MQTT fan-out");
      }
      return match;
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error("Core did not record the MQTT event before timeout");
}

await api("/mqtt/events", { method: "DELETE" });
await fetch("http://localhost:9100/test/reset", { method: "DELETE" });
await publish();
const event = await waitForCoreEvent();

const result = {
  published: { brokerUrl, topic, qos: 1, eventId },
  received: {
    status: event.status,
    normalizedDeviceId: event.normalizedRequest.deviceId,
    outcome: event.result.outcome,
    reasonCode: event.result.reasonCode,
    analytics: event.deliveries.analytics.status,
    notification: event.deliveries.notification.status
  }
};

await mkdir(dirname(reportPath), { recursive: true });
await writeFile(reportPath, `${JSON.stringify(result, null, 2)}\n`);
console.log(JSON.stringify(result, null, 2));
