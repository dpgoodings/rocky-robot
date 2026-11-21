/*
 * ESP32 Servo Autoclicker with Web Dashboard & REST API
 * 
 * Hardware:
 *   - ESP32 Development Board (ESP32-WROOM-32, ESP32-S3, ESP32-C3, etc.)
 *   - Micro Servo Motor (SG90, MG90S, or similar 9g servo)
 * 
 * Pinout:
 *   - Servo Signal (Orange / Yellow) -> GPIO 18 (default, configurable)
 *   - Servo VCC (Red)               -> 5V / VIN pin (recommended external power if servo draws >500mA)
 *   - Servo GND (Brown / Black)     -> ESP32 GND
 */

#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>
#include <Preferences.h>
#include <ESP32Servo.h>

// ======================== HARDWARE CONFIG ========================
#define SERVO_PIN 18         // GPIO pin connected to servo PWM
#define STATUS_LED_PIN 2     // On-board LED (GPIO 2 on most ESP32 boards)

// Default Servo Timing Constants (Microseconds)
#define MIN_PULSE_WIDTH 500  // Minimum pulse width in microseconds (500us for 0 deg)
#define MAX_PULSE_WIDTH 2500 // Maximum pulse width in microseconds (2500us for 180 deg)

// ======================== GLOBAL OBJECTS ========================
Servo clickServo;
WebServer server(80);
Preferences preferences;

// ======================== RUNTIME SETTINGS ========================
struct ClickerConfig {
  int restAngle;         // Degrees (default 20)
  int pressAngle;        // Degrees (default 75)
  int holdDurationMs;    // Milliseconds to hold button down (default 200)
  int intervalSeconds;   // Seconds between auto-clicks (default 5)
  int jitterSeconds;     // Random variation (+/- seconds, default 0)
  bool autoDetach;       // Detach PWM when idle to eliminate servo hum & save power
  char wifiSsid[64];
  char wifiPassword[64];
};

ClickerConfig config;

// ======================== STATE MACHINE ========================
enum ClickerState {
  STATE_IDLE,
  STATE_ARM_PRESSING,
  STATE_ARM_RETRACTING
};

ClickerState currentState = STATE_IDLE;
bool isAutoClicking = false;
unsigned long totalClicks = 0;
unsigned long lastClickTime = 0;
unsigned long nextTargetIntervalMs = 0;
unsigned long stateStartTime = 0;
bool singleClickRequested = false;

// ======================== FORWARD DECLARATIONS ========================
void loadSettings();
void saveSettings();
void startServo();
void stopServo();
void setServoAngle(int angle);
void triggerClick();
void handleStateMachine();
void setupWiFi();
void setupWebServer();

// ======================== WEB DASHBOARD HTML ========================
const char INDEX_HTML[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
<title>ESP32 Servo Autoclicker</title>
<style>
  :root {
    --bg: #0f172a;
    --card: #1e293b;
    --card-border: #334155;
    --text: #f8fafc;
    --text-dim: #94a3b8;
    --primary: #38bdf8;
    --primary-hover: #0284c7;
    --success: #22c55e;
    --danger: #ef4444;
    --accent: #a855f7;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
  body { background: var(--bg); color: var(--text); min-height: 100vh; padding: 1.25rem; display: flex; justify-content: center; }
  .container { width: 100%; max-width: 520px; }
  header { text-align: center; margin-bottom: 1.5rem; }
  header h1 { font-size: 1.6rem; font-weight: 700; color: #fff; display: flex; align-items: center; justify-content: center; gap: 0.5rem; }
  header p { color: var(--text-dim); font-size: 0.85rem; margin-top: 0.25rem; }
  
  .card { background: var(--card); border: 1px solid var(--card-border); border-radius: 1rem; padding: 1.25rem; margin-bottom: 1.25rem; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.2); }
  .card-title { font-size: 1rem; font-weight: 600; margin-bottom: 1rem; display: flex; align-items: center; justify-content: space-between; }
  
  .status-badge { display: inline-flex; align-items: center; gap: 0.4rem; padding: 0.25rem 0.75rem; border-radius: 9999px; font-size: 0.75rem; font-weight: 600; text-transform: uppercase; }
  .status-idle { background: rgba(148, 163, 184, 0.15); color: #94a3b8; border: 1px solid #475569; }
  .status-running { background: rgba(34, 197, 94, 0.15); color: #4ade80; border: 1px solid #22c55e; }
  .status-dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; }
  .pulse { animation: pulse 1.5s infinite; }
  @keyframes pulse { 0% { opacity: 0.4; } 50% { opacity: 1; } 100% { opacity: 0.4; } }
  
  .stat-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; text-align: center; }
  .stat-box { background: rgba(15, 23, 42, 0.6); padding: 0.85rem; border-radius: 0.75rem; border: 1px solid rgba(255,255,255,0.05); }
  .stat-val { font-size: 1.5rem; font-weight: 700; color: var(--primary); }
  .stat-label { font-size: 0.75rem; color: var(--text-dim); text-transform: uppercase; margin-top: 0.2rem; }

  .btn-group { display: flex; gap: 0.75rem; margin-top: 1rem; }
  button { flex: 1; padding: 0.85rem 1rem; border: none; border-radius: 0.75rem; font-size: 0.95rem; font-weight: 600; cursor: pointer; transition: all 0.15s ease; display: inline-flex; align-items: center; justify-content: center; gap: 0.5rem; }
  button:active { transform: scale(0.98); }
  .btn-primary { background: var(--primary); color: #0f172a; }
  .btn-primary:hover { background: var(--primary-hover); }
  .btn-start { background: var(--success); color: #0f172a; }
  .btn-stop { background: var(--danger); color: #fff; }
  .btn-outline { background: transparent; border: 1px solid var(--card-border); color: var(--text); }
  .btn-outline:hover { background: rgba(255,255,255,0.05); }

  .form-group { margin-bottom: 1rem; }
  .form-label { display: flex; justify-content: space-between; font-size: 0.85rem; margin-bottom: 0.4rem; color: var(--text); }
  .form-label span.val { font-weight: 600; color: var(--primary); }
  input[type="range"] { width: 100%; accent-color: var(--primary); cursor: pointer; }
  input[type="number"], input[type="text"], input[type="password"] { width: 100%; background: #0f172a; border: 1px solid var(--card-border); border-radius: 0.5rem; padding: 0.6rem 0.8rem; color: #fff; font-size: 0.9rem; }
  .checkbox-label { display: flex; align-items: center; gap: 0.6rem; font-size: 0.85rem; cursor: pointer; }
  .checkbox-label input { width: 1.1rem; height: 1.1rem; accent-color: var(--primary); }
  
  .calib-btns { display: flex; gap: 0.5rem; margin-top: 0.3rem; }
  .calib-btns button { font-size: 0.75rem; padding: 0.4rem; }

  footer { text-align: center; font-size: 0.75rem; color: var(--text-dim); margin-top: 1rem; }
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>🤖 ESP32 Autoclicker</h1>
    <p>Mechanical Button Press Automator</p>
  </header>

  <!-- Live Status Card -->
  <div class="card">
    <div class="card-title">
      <span>Status</span>
      <span id="badge" class="status-badge status-idle"><span class="status-dot"></span><span id="badge-text">Idle</span></span>
    </div>
    <div class="stat-grid">
      <div class="stat-box">
        <div id="click-count" class="stat-val">0</div>
        <div class="stat-label">Clicks Done</div>
      </div>
      <div class="stat-box">
        <div id="countdown" class="stat-val">--</div>
        <div class="stat-label">Next Click In</div>
      </div>
    </div>
    <div class="btn-group">
      <button class="btn-primary" onclick="triggerManualClick()">👉 Click Once</button>
      <button id="toggle-auto-btn" class="btn-start" onclick="toggleAuto()">▶ Start Auto</button>
    </div>
  </div>

  <!-- Calibration & Servo Setup -->
  <div class="card">
    <div class="card-title">
      <span>Servo Angles Calibration</span>
    </div>
    
    <div class="form-group">
      <div class="form-label">
        <span>Rest Angle (Raised)</span>
        <span class="val"><span id="rest-val">20</span>°</span>
      </div>
      <input type="range" id="restAngle" min="0" max="180" value="20" oninput="updateVal('rest', this.value)">
      <div class="calib-btns">
        <button class="btn-outline" onclick="calibrateTo('rest')">Test Rest Position</button>
      </div>
    </div>

    <div class="form-group">
      <div class="form-label">
        <span>Press Angle (Button Contact)</span>
        <span class="val"><span id="press-val">75</span>°</span>
      </div>
      <input type="range" id="pressAngle" min="0" max="180" value="75" oninput="updateVal('press', this.value)">
      <div class="calib-btns">
        <button class="btn-outline" onclick="calibrateTo('press')">Test Press Position</button>
      </div>
    </div>

    <div class="form-group">
      <div class="form-label">
        <span>Press Hold Duration</span>
        <span class="val"><span id="hold-val">200</span> ms</span>
      </div>
      <input type="range" id="holdDurationMs" min="50" max="2000" step="50" value="200" oninput="updateVal('hold', this.value)">
    </div>

    <div class="form-group">
      <label class="checkbox-label">
        <input type="checkbox" id="autoDetach">
        <span>Auto-detach servo when idle (stops buzzing &amp; heat)</span>
      </label>
    </div>
  </div>

  <!-- Timing Schedule -->
  <div class="card">
    <div class="card-title">
      <span>Auto-Click Schedule</span>
    </div>
    
    <div class="form-group">
      <div class="form-label">
        <span>Interval Between Clicks (seconds)</span>
      </div>
      <input type="number" id="intervalSeconds" min="1" max="86400" value="5">
    </div>

    <div class="form-group">
      <div class="form-label">
        <span>Random Jitter &plusmn; (seconds)</span>
      </div>
      <input type="number" id="jitterSeconds" min="0" max="60" value="0">
      <small style="color:var(--text-dim); font-size:0.75rem;">Adds human-like random variance to avoid bot detection</small>
    </div>

    <button class="btn-primary" style="width:100%;" onclick="saveSettings()">💾 Save Settings</button>
  </div>

  <!-- Wi-Fi Network Setup -->
  <div class="card">
    <div class="card-title">
      <span>Wi-Fi Configuration</span>
    </div>
    <div class="form-group">
      <label class="form-label">SSID (Network Name)</label>
      <input type="text" id="wifiSsid" placeholder="Your Wi-Fi SSID">
    </div>
    <div class="form-group">
      <label class="form-label">Password</label>
      <input type="password" id="wifiPassword" placeholder="Your Wi-Fi Password">
    </div>
    <button class="btn-outline" style="width:100%;" onclick="saveWifi()">📶 Update Wi-Fi &amp; Reboot</button>
  </div>

  <footer>
    ESP32 Servo Autoclicker &bull; mDNS: http://autoclicker.local
  </footer>
</div>

<script>
let autoActive = false;
let countdownRemaining = 0;

function updateVal(type, val) {
  document.getElementById(type + '-val').innerText = val;
}

function fetchStatus() {
  fetch('/api/status')
    .then(r => r.json())
    .then(d => {
      autoActive = d.auto;
      document.getElementById('click-count').innerText = d.clicks;
      
      const badge = document.getElementById('badge');
      const badgeText = document.getElementById('badge-text');
      const toggleBtn = document.getElementById('toggle-auto-btn');

      if (d.auto) {
        badge.className = 'status-badge status-running';
        badgeText.innerText = 'Active';
        toggleBtn.className = 'btn-stop';
        toggleBtn.innerText = '⏹ Stop Auto';
        countdownRemaining = d.next_in;
        document.getElementById('countdown').innerText = countdownRemaining > 0 ? (countdownRemaining + 's') : 'Now';
      } else {
        badge.className = 'status-badge status-idle';
        badgeText.innerText = 'Idle';
        toggleBtn.className = 'btn-start';
        toggleBtn.innerText = '▶ Start Auto';
        document.getElementById('countdown').innerText = '--';
      }
    }).catch(e => console.error("Poll error", e));
}

function triggerManualClick() {
  fetch('/api/click', { method: 'POST' }).then(() => fetchStatus());
}

function toggleAuto() {
  const endpoint = autoActive ? '/api/stop' : '/api/start';
  fetch(endpoint, { method: 'POST' }).then(() => fetchStatus());
}

function calibrateTo(target) {
  const angle = (target === 'rest') ? document.getElementById('restAngle').value : document.getElementById('pressAngle').value;
  fetch('/api/calibrate?angle=' + angle, { method: 'POST' });
}

function saveSettings() {
  const payload = {
    restAngle: parseInt(document.getElementById('restAngle').value),
    pressAngle: parseInt(document.getElementById('pressAngle').value),
    holdDurationMs: parseInt(document.getElementById('holdDurationMs').value),
    intervalSeconds: parseInt(document.getElementById('intervalSeconds').value),
    jitterSeconds: parseInt(document.getElementById('jitterSeconds').value),
    autoDetach: document.getElementById('autoDetach').checked
  };
  fetch('/api/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  }).then(r => r.json()).then(res => {
    alert('Settings saved successfully!');
    fetchStatus();
  });
}

function saveWifi() {
  const ssid = document.getElementById('wifiSsid').value;
  const pass = document.getElementById('wifiPassword').value;
  if (!ssid) { alert('SSID is required'); return; }
  if (confirm('ESP32 will save Wi-Fi credentials and reboot. Continue?')) {
    fetch('/api/wifi', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ssid: ssid, password: pass })
    }).then(() => {
      alert('Connecting to ' + ssid + '... Check your router or http://autoclicker.local');
    });
  }
}

// Load initial config from ESP32
function loadInitialConfig() {
  fetch('/api/config')
    .then(r => r.json())
    .then(c => {
      document.getElementById('restAngle').value = c.restAngle;
      updateVal('rest', c.restAngle);
      document.getElementById('pressAngle').value = c.pressAngle;
      updateVal('press', c.pressAngle);
      document.getElementById('holdDurationMs').value = c.holdDurationMs;
      updateVal('hold', c.holdDurationMs);
      document.getElementById('intervalSeconds').value = c.intervalSeconds;
      document.getElementById('jitterSeconds').value = c.jitterSeconds;
      document.getElementById('autoDetach').checked = c.autoDetach;
      document.getElementById('wifiSsid').value = c.wifiSsid || '';
    });
}

window.onload = () => {
  loadInitialConfig();
  fetchStatus();
  setInterval(fetchStatus, 1000);
};
</script>
</body>
</html>
)rawliteral";

// ======================== HARDWARE / SERVO LOGIC ========================
void startServo() {
  if (!clickServo.attached()) {
    clickServo.setPeriodHertz(50); // Standard 50Hz servo
    clickServo.attach(SERVO_PIN, MIN_PULSE_WIDTH, MAX_PULSE_WIDTH);
  }
}

void stopServo() {
  if (config.autoDetach && clickServo.attached()) {
    clickServo.detach();
  }
}

void setServoAngle(int angle) {
  startServo();
  clickServo.write(constrain(angle, 0, 180));
}

void calculateNextInterval() {
  int jitter = 0;
  if (config.jitterSeconds > 0) {
    jitter = random(-config.jitterSeconds, config.jitterSeconds + 1);
  }
  long sec = config.intervalSeconds + jitter;
  if (sec < 1) sec = 1;
  nextTargetIntervalMs = sec * 1000L;
}

void triggerClick() {
  if (currentState == STATE_IDLE) {
    currentState = STATE_ARM_PRESSING;
    stateStartTime = millis();
    setServoAngle(config.pressAngle);
    digitalWrite(STATUS_LED_PIN, HIGH);
  }
}

void handleStateMachine() {
  unsigned long now = millis();

  // 1. Process active press/retract state transitions
  switch (currentState) {
    case STATE_ARM_PRESSING:
      if (now - stateStartTime >= (unsigned long)config.holdDurationMs) {
        // Switch to retract
        currentState = STATE_ARM_RETRACTING;
        stateStartTime = now;
        setServoAngle(config.restAngle);
      }
      break;

    case STATE_ARM_RETRACTING:
      // Allow 250ms for servo horn to physically return to rest position
      if (now - stateStartTime >= 250) {
        currentState = STATE_IDLE;
        totalClicks++;
        lastClickTime = now;
        digitalWrite(STATUS_LED_PIN, LOW);
        calculateNextInterval();
        stopServo();
      }
      break;

    case STATE_IDLE:
      // Check for single click trigger request
      if (singleClickRequested) {
        singleClickRequested = false;
        triggerClick();
      } 
      // Check auto-click schedule
      else if (isAutoClicking) {
        if (now - lastClickTime >= nextTargetIntervalMs) {
          triggerClick();
        }
      }
      break;
  }
}

// ======================== PERSISTENCE (FLASH) ========================
void loadSettings() {
  preferences.begin("autoclicker", false);
  config.restAngle = preferences.getInt("restAngle", 20);
  config.pressAngle = preferences.getInt("pressAngle", 75);
  config.holdDurationMs = preferences.getInt("holdMs", 200);
  config.intervalSeconds = preferences.getInt("intervalSec", 5);
  config.jitterSeconds = preferences.getInt("jitterSec", 0);
  config.autoDetach = preferences.getBool("autoDetach", true);

  String ssid = preferences.getString("ssid", "");
  String pass = preferences.getString("pass", "");
  strncpy(config.wifiSsid, ssid.c_str(), sizeof(config.wifiSsid));
  strncpy(config.wifiPassword, pass.c_str(), sizeof(config.wifiPassword));
  preferences.end();
}

void saveSettings() {
  preferences.begin("autoclicker", false);
  preferences.putInt("restAngle", config.restAngle);
  preferences.putInt("pressAngle", config.pressAngle);
  preferences.putInt("holdMs", config.holdDurationMs);
  preferences.putInt("intervalSec", config.intervalSeconds);
  preferences.putInt("jitterSec", config.jitterSeconds);
  preferences.putBool("autoDetach", config.autoDetach);
  preferences.end();
}

void saveWifiCredentials(const String& ssid, const String& pass) {
  preferences.begin("autoclicker", false);
  preferences.putString("ssid", ssid);
  preferences.putString("pass", pass);
  preferences.end();
}

// ======================== NETWORK & WEB HANDLERS ========================
void setupWiFi() {
  WiFi.mode(WIFI_STA);
  
  if (strlen(config.wifiSsid) > 0) {
    Serial.printf("Connecting to Wi-Fi SSID: %s\n", config.wifiSsid);
    WiFi.begin(config.wifiSsid, config.wifiPassword);
    
    int attempts = 0;
    while (WiFi.status() != WL_CONNECTED && attempts < 20) {
      delay(500);
      Serial.print(".");
      attempts++;
    }
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\nWiFi Connected!");
    Serial.print("IP Address: ");
    Serial.println(WiFi.localIP());
  } else {
    // Fallback to SoftAP mode if no connection or credentials empty
    Serial.println("\nStarting SoftAP fallback mode...");
    WiFi.mode(WIFI_AP);
    WiFi.softAP("ESP32-Autoclicker", "12345678");
    Serial.print("Access Point created. Connect to 'ESP32-Autoclicker' password '12345678'\nAP IP: ");
    Serial.println(WiFi.softAPIP());
  }

  // Register mDNS
  if (MDNS.begin("autoclicker")) {
    Serial.println("mDNS responder started: http://autoclicker.local");
    MDNS.addService("http", "tcp", 80);
  }
}

void setupWebServer() {
  // Main Web UI
  server.on("/", HTTP_GET, []() {
    server.send_P(200, "text/html", INDEX_HTML);
  });

  // REST API: Status
  server.on("/api/status", HTTP_GET, []() {
    unsigned long now = millis();
    int nextIn = 0;
    if (isAutoClicking && nextTargetIntervalMs > (now - lastClickTime)) {
      nextIn = (nextTargetIntervalMs - (now - lastClickTime)) / 1000;
    }

    String json = "{";
    json += "\"auto\":" + String(isAutoClicking ? "true" : "false") + ",";
    json += "\"clicks\":" + String(totalClicks) + ",";
    json += "\"next_in\":" + String(nextIn) + ",";
    json += "\"state\":\"" + String(currentState == STATE_IDLE ? "IDLE" : "PRESSING") + "\"";
    json += "}";
    server.send(200, "application/json", json);
  });

  // REST API: Trigger 1 Click
  server.on("/api/click", HTTP_POST, []() {
    singleClickRequested = true;
    server.send(200, "application/json", "{\"status\":\"ok\",\"action\":\"click\"}");
  });

  // REST API: Start Auto
  server.on("/api/start", HTTP_POST, []() {
    isAutoClicking = true;
    lastClickTime = millis();
    calculateNextInterval();
    server.send(200, "application/json", "{\"status\":\"ok\",\"auto\":true}");
  });

  // REST API: Stop Auto
  server.on("/api/stop", HTTP_POST, []() {
    isAutoClicking = false;
    server.send(200, "application/json", "{\"status\":\"ok\",\"auto\":false}");
  });

  // REST API: Calibration direct move
  server.on("/api/calibrate", HTTP_POST, []() {
    if (server.hasArg("angle")) {
      int angle = server.arg("angle").toInt();
      setServoAngle(angle);
      server.send(200, "application/json", "{\"status\":\"ok\",\"angle\":" + String(angle) + "}");
    } else {
      server.send(400, "application/json", "{\"error\":\"missing angle\"}");
    }
  });

  // REST API: Get Config
  server.on("/api/config", HTTP_GET, []() {
    String json = "{";
    json += "\"restAngle\":" + String(config.restAngle) + ",";
    json += "\"pressAngle\":" + String(config.pressAngle) + ",";
    json += "\"holdDurationMs\":" + String(config.holdDurationMs) + ",";
    json += "\"intervalSeconds\":" + String(config.intervalSeconds) + ",";
    json += "\"jitterSeconds\":" + String(config.jitterSeconds) + ",";
    json += "\"autoDetach\":" + String(config.autoDetach ? "true" : "false") + ",";
    json += "\"wifiSsid\":\"" + String(config.wifiSsid) + "\"";
    json += "}";
    server.send(200, "application/json", json);
  });

  // REST API: Save Settings
  server.on("/api/settings", HTTP_POST, []() {
    if (server.hasArg("plain")) {
      String body = server.arg("plain");
      
      // Simple parser for key JSON fields
      auto extractInt = [&](const char* key, int def) -> int {
        int pos = body.indexOf(key);
        if (pos == -1) return def;
        pos += strlen(key) + 2; // skip key":
        while (pos < body.length() && (body[pos] == ' ' || body[pos] == ':')) pos++;
        return body.substring(pos).toInt();
      };

      config.restAngle = extractInt("restAngle", config.restAngle);
      config.pressAngle = extractInt("pressAngle", config.pressAngle);
      config.holdDurationMs = extractInt("holdDurationMs", config.holdDurationMs);
      config.intervalSeconds = extractInt("intervalSeconds", config.intervalSeconds);
      config.jitterSeconds = extractInt("jitterSeconds", config.jitterSeconds);
      if (body.indexOf("\"autoDetach\":true") != -1) config.autoDetach = true;
      if (body.indexOf("\"autoDetach\":false") != -1) config.autoDetach = false;

      saveSettings();
      calculateNextInterval();
      server.send(200, "application/json", "{\"status\":\"saved\"}");
    } else {
      server.send(400, "application/json", "{\"error\":\"missing body\"}");
    }
  });

  // REST API: Save Wi-Fi & Restart
  server.on("/api/wifi", HTTP_POST, []() {
    if (server.hasArg("plain")) {
      String body = server.arg("plain");
      int sPos = body.indexOf("\"ssid\":");
      int pPos = body.indexOf("\"password\":");
      if (sPos != -1) {
        int sStart = body.indexOf("\"", sPos + 7) + 1;
        int sEnd = body.indexOf("\"", sStart);
        String ssid = body.substring(sStart, sEnd);

        String pass = "";
        if (pPos != -1) {
          int pStart = body.indexOf("\"", pPos + 11) + 1;
          int pEnd = body.indexOf("\"", pStart);
          pass = body.substring(pStart, pEnd);
        }

        saveWifiCredentials(ssid, pass);
        server.send(200, "application/json", "{\"status\":\"rebooting\"}");
        delay(1000);
        ESP.restart();
        return;
      }
    }
    server.send(400, "application/json", "{\"error\":\"invalid wifi payload\"}");
  });

  server.begin();
  Serial.println("HTTP Web Server ready.");
}

// ======================== ARDUINO SETUP & LOOP ========================
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n--- ESP32 Servo Autoclicker Initializing ---");

  pinMode(STATUS_LED_PIN, OUTPUT);
  digitalWrite(STATUS_LED_PIN, LOW);

  // Load preferences from flash
  loadSettings();
  calculateNextInterval();

  // Allow allocation of all timers for servo
  ESP32PWM::allocateTimer(0);
  ESP32PWM::allocateTimer(1);
  ESP32PWM::allocateTimer(2);
  ESP32PWM::allocateTimer(3);

  // Initial move to rest position
  setServoAngle(config.restAngle);
  delay(300);
  stopServo();

  // Network & Web Server
  setupWiFi();
  setupWebServer();

  Serial.println("Ready!");
}

void loop() {
  server.handleClient();
  handleStateMachine();
}
