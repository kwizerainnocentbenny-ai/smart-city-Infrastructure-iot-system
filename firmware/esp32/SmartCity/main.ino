/*
 * SMART CITY IoT - ESP32 COMPLETE CONTROLLER v2.7
 * ================================================
 * FIX v2.7:  REAL-TIME distance display - NO DELAY
 * 
 * CHANGES:
 * - Send every reading regardless of change
 * - Faster dustbin reading interval (2 seconds)
 * - Removed change detection filter
 * - Always show latest distance
 * 
 * Pin summary:
 *   Street Light  → GPIO 25
 *   DHT11         → GPIO 32
 *   Irrigation    → GPIO 26
 *   Livestock     → GPIO 27
 *   Soil Moisture → GPIO 34 (ADC)
 *   HC-SR04 TRIG  → GPIO 18
 *   HC-SR04 ECHO  → GPIO 19
 * ================================================
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <DHT.h>

// ==================== WiFi ====================
const char* ssid      = "KWIZERA";
const char* password  = "Kinno@123";
const char* serverUrl = "http://192.168.137.1:8001/api";

// ==================== Pins ====================
#define STREET_LIGHT_PIN    25
#define DHTPIN              32
#define DHTTYPE             DHT11
#define IRRIGATION_PUMP_PIN 26
#define LIVESTOCK_PUMP_PIN  27
#define WATER_SENSOR_PIN    34
#define TRIG_PIN            18
#define ECHO_PIN            19

// ==================== Device IDs ====================
const int IRRIGATION_PUMP_ID   = 1;
const int LIVESTOCK_PUMP_ID    = 2;
const int SOIL_MOISTURE_ID     = 3;
const int DHT11_SENSOR_ID      = 4;
const int STREET_LIGHT_ZONE_ID = 1;
const int DUSTBIN_DEVICE_ID    = 1;

// ==================== Dustbin config ====================
const float BIN_HEIGHT_CM      = 28.0;
const int   MAX_SENSOR_ATTEMPTS = 3;
const int   PULSE_TIMEOUT       = 50000;

// ==================== Timing ====================
const unsigned long POLL_INTERVAL      = 300;    // 300ms for pumps/lights
const unsigned long DHT_INTERVAL       = 5000;
const unsigned long SOIL_INTERVAL      = 5000;
const unsigned long DUSTBIN_INTERVAL   = 2000;   // 2 seconds for real-time updates
const unsigned long HEARTBEAT_INTERVAL = 30000;

// ==================== State ====================
bool  streetLightState      = false;
int   streetLightBrightness = 100;
bool  irrigationState       = false;
bool  livestockState        = false;
float lastTemperature       = -1;
float lastHumidity          = -1;

unsigned long lastPoll      = 0;
unsigned long lastDHTRead   = 0;
unsigned long lastSoilRead  = 0;
unsigned long lastBinRead   = 0;
unsigned long lastHeartbeat = 0;

bool lastLightState      = false;
int  lastLightBrightness = 100;

DHT dht(DHTPIN, DHTTYPE);

// ============================================================
//  Fast HTTP GET
// ============================================================
int httpGET(const String& url, String* responseBody = nullptr) {
  WiFiClient client;
  HTTPClient http;
  if (!http.begin(client, url)) return -1;
  http.setTimeout(300);
  int code = http.GET();
  if (responseBody && code > 0) *responseBody = http.getString();
  http.end();
  return code;
}

int httpPOST(const String& url, const String& body) {
  WiFiClient client;
  HTTPClient http;
  if (!http.begin(client, url)) return -1;
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(1000);
  int code = http.POST(body);
  http.end();
  return code;
}

int httpPUT(const String& url, const String& body) {
  WiFiClient client;
  HTTPClient http;
  if (!http.begin(client, url)) return -1;
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(1000);
  int code = http.PUT(body);
  http.end();
  return code;
}

// ============================================================
//  IMPROVED ULTRASONIC READING
// ============================================================
float measureDistance() {
  float distances[MAX_SENSOR_ATTEMPTS];
  int validReadings = 0;
  
  for (int attempt = 0; attempt < MAX_SENSOR_ATTEMPTS; attempt++) {
    digitalWrite(TRIG_PIN, LOW);
    delayMicroseconds(2);
    digitalWrite(TRIG_PIN, HIGH);
    delayMicroseconds(10);
    digitalWrite(TRIG_PIN, LOW);

    long duration = pulseIn(ECHO_PIN, HIGH, PULSE_TIMEOUT);
    
    if (duration > 0) {
      float dist = duration * 0.0343f / 2.0f;
      
      if (dist >= 2.0f && dist <= 400.0f) {
        distances[validReadings++] = dist;
      }
    }
    
    delay(10);
  }
  
  if (validReadings == 0) {
    return -1.0f;
  }
  
  float sum = 0;
  for (int i = 0; i < validReadings; i++) {
    sum += distances[i];
  }
  
  return sum / validReadings;
}

// ============================================================
void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println("\n============================================");
  Serial.println(" SMART CITY IoT v2.7  —  REAL-TIME DUSTBIN");
  Serial.println("============================================");
  Serial.println(" ✓ Distance updates every 2 seconds");
  Serial.println(" ✓ No filtering - shows raw data");
  Serial.println("--------------------------------------------");

  pinMode(STREET_LIGHT_PIN,    OUTPUT);
  pinMode(IRRIGATION_PUMP_PIN, OUTPUT);
  pinMode(LIVESTOCK_PUMP_PIN,  OUTPUT);
  pinMode(TRIG_PIN,            OUTPUT);
  pinMode(ECHO_PIN,            INPUT);
  pinMode(WATER_SENSOR_PIN,    INPUT);
  
  digitalWrite(TRIG_PIN,            LOW);
  digitalWrite(STREET_LIGHT_PIN,    LOW);
  digitalWrite(IRRIGATION_PUMP_PIN, LOW);
  digitalWrite(LIVESTOCK_PUMP_PIN,  LOW);

  dht.begin();

  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);
  Serial.print("Connecting to WiFi");
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 40) {
    delay(500); 
    Serial.print("."); 
    attempts++;
  }
  
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n✅ WiFi Connected!");
    Serial.print("   IP: ");
    Serial.println(WiFi.localIP());
  }
  
  Serial.println("============================================\n");
}

// ============================================================
void loop() {
  unsigned long now = millis();

  if (WiFi.status() != WL_CONNECTED) {
    static unsigned long lastRecon = 0;
    if (now - lastRecon > 10000) {
      WiFi.reconnect();
      lastRecon = now;
    }
    delay(100);
    return;
  }

  // Fast polling for pumps and lights
  if (now - lastPoll >= POLL_INTERVAL) {
    checkLightCommand();
    checkIrrigationPump();
    checkLivestockPump();
    lastPoll = now;
  }

  // DHT11 sensor
  if (now - lastDHTRead >= DHT_INTERVAL) {
    readAndSendDHT();
    lastDHTRead = now;
  }

  // Soil moisture sensor
  if (now - lastSoilRead >= SOIL_INTERVAL) {
    readAndSendSoil();
    lastSoilRead = now;
  }

  // DUSTBIN - REAL-TIME UPDATES every 2 seconds
  if (now - lastBinRead >= DUSTBIN_INTERVAL) {
    readAndSendDustbin();
    lastBinRead = now;
  }

  // Heartbeat
  if (now - lastHeartbeat >= HEARTBEAT_INTERVAL) {
    sendHeartbeat(IRRIGATION_PUMP_ID);
    sendHeartbeat(LIVESTOCK_PUMP_ID);
    sendHeartbeat(SOIL_MOISTURE_ID);
    sendHeartbeat(DHT11_SENSOR_ID);
    sendHeartbeat(STREET_LIGHT_ZONE_ID);
    sendHeartbeat(DUSTBIN_DEVICE_ID);
    lastHeartbeat = now;
  }

  delay(5);
}

// ============================================================
//  STREET LIGHT
// ============================================================
void checkLightCommand() {
  String url = String(serverUrl) + "/lighting/command/" + String(STREET_LIGHT_ZONE_ID);
  String body;
  int code = httpGET(url, &body);

  if (code != 200) return;

  StaticJsonDocument<200> doc;
  if (deserializeJson(doc, body)) return;

  const char* cmd    = doc["state"];
  int         bright = doc["brightness"] | 100;
  if (!cmd) return;

  bool newState = (strcmp(cmd, "ON") == 0);
  if (newState != lastLightState || bright != lastLightBrightness) {
    digitalWrite(STREET_LIGHT_PIN, newState ? HIGH : LOW);
    streetLightState      = newState;
    streetLightBrightness = bright;
    lastLightState        = newState;
    lastLightBrightness   = bright;
    Serial.printf("💡 Street Light → %s\n", newState ? "ON" : "OFF");
  }
}

// ============================================================
//  PUMPS
// ============================================================
void checkIrrigationPump() {
  String url = String(serverUrl) + "/esp32/command/" + String(IRRIGATION_PUMP_ID);
  String body;
  int code = httpGET(url, &body);

  if (code != 200) return;

  StaticJsonDocument<200> doc;
  if (deserializeJson(doc, body)) return;

  const char* cmd = doc["command"];
  if (!cmd) return;

  bool target = (strcmp(cmd, "ON") == 0);

  if (target != irrigationState) {
    digitalWrite(IRRIGATION_PUMP_PIN, target ? HIGH : LOW);
    irrigationState = target;
    Serial.printf("💧 Irrigation Pump → %s\n", target ? "ON" : "OFF");
  }
}

void checkLivestockPump() {
  String url = String(serverUrl) + "/esp32/command/" + String(LIVESTOCK_PUMP_ID);
  String body;
  int code = httpGET(url, &body);

  if (code != 200) return;

  StaticJsonDocument<200> doc;
  if (deserializeJson(doc, body)) return;

  const char* cmd = doc["command"];
  if (!cmd) return;

  bool target = (strcmp(cmd, "ON") == 0);

  if (target != livestockState) {
    digitalWrite(LIVESTOCK_PUMP_PIN, target ? HIGH : LOW);
    livestockState = target;
    Serial.printf("🐄 Livestock Pump → %s\n", target ? "ON" : "OFF");
  }
}

// ============================================================
//  DHT11
// ============================================================
void readAndSendDHT() {
  float hum = dht.readHumidity();
  float tmp = dht.readTemperature();
  if (isnan(hum) || isnan(tmp)) return;

  StaticJsonDocument<200> doc;
  doc["device_id"]   = DHT11_SENSOR_ID;
  doc["temperature"] = tmp;
  doc["humidity"]    = hum;
  String body; 
  serializeJson(doc, body);

  httpPOST(String(serverUrl) + "/industrial/temperature", body);
  
  // Print for debugging
  Serial.printf("🌡️ Temperature: %.1f°C, Humidity: %.1f%%\n", tmp, hum);
}

// ============================================================
//  SOIL MOISTURE
// ============================================================
void readAndSendSoil() {
  int raw     = analogRead(WATER_SENSOR_PIN);
  int percent = constrain(map(raw, 0, 3800, 0, 100), 0, 100);

  StaticJsonDocument<100> doc;
  char buf[10]; 
  snprintf(buf, sizeof(buf), "%d%%", percent);
  doc["value"] = buf;
  String body; 
  serializeJson(doc, body);

  httpPUT(String(serverUrl) + "/sensor/" + String(SOIL_MOISTURE_ID) + "/reading", body);
  
  Serial.printf("🌱 Soil Moisture: %d%%\n", percent);
}

// ============================================================
//  DUSTBIN - REAL-TIME UPDATES (EVERY READING)
// ============================================================
void readAndSendDustbin() {
  float dist = measureDistance();
  
  if (dist < 0) {
    Serial.println("⚠️ Ultrasonic sensor: No reading");
    return;
  }

  float fill = (dist >= BIN_HEIGHT_CM) ? 0 : 
               (dist <= 0) ? 100 : 
               constrain(((BIN_HEIGHT_CM - dist) / BIN_HEIGHT_CM) * 100.0f, 0.0f, 100.0f);

  // Print EVERY reading - NO FILTERING
  Serial.println("────────────────────────────────");
  Serial.printf("🗑️ DUSTBIN REAL-TIME:\n");
  Serial.printf("   Distance: %.1f cm\n", dist);
  Serial.printf("   Fill:     %.1f%%\n", fill);
  Serial.printf("   Status:   %s\n", 
                fill >= 90 ? "FULL" : 
                fill >= 70 ? "NEAR FULL" : "NORMAL");
  Serial.println("────────────────────────────────");

  // Send to backend - EVERY TIME
  StaticJsonDocument<200> doc;
  doc["device_id"]    = DUSTBIN_DEVICE_ID;
  doc["distance_cm"]  = dist;
  String body; 
  serializeJson(doc, body);

  int code = httpPOST(String(serverUrl) + "/dustbin/reading", body);
  
  if (code == 200 || code == 201) {
    Serial.println("   ✅ Data sent to server");
  } else {
    Serial.printf("   ❌ Send failed (HTTP %d)\n", code);
  }
}

// ============================================================
//  HEARTBEAT
// ============================================================
void sendHeartbeat(int deviceId) {
  WiFiClient client;
  HTTPClient http;
  String url = String(serverUrl) + "/esp32/heartbeat/" + String(deviceId);
  if (http.begin(client, url)) {
    http.setTimeout(300);
    http.POST("");
    http.end();
  }
}
