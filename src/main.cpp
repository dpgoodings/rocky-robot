/**
 * Rocky-S3 Autonomous Robot Edge Firmware
 * Hardware: ESP32-S3 WROOM (16MB Flash, 8MB PSRAM, OV2640, MicroSD via SDMMC)
 * 
 * Features:
 * - Asynchronous Non-Blocking HTTP Server (esp_http_server)
 * - Concurrent streaming on Port 80 & Port 81 without blocking commands
 * - Hardware Self-Test: 8MB PSRAM validation & diagnostics
 * - MicroSD Storage: 1-bit SDMMC high-speed card mount, logging & photo capture
 * - Camera Engine: Hardware GDMA accelerated JPEG capture in PSRAM
 * - Web Server: Live MJPEG stream, snapshot API, telemetry & REST action interface
 * - Sensor Image Optimization: Auto-exposure, auto-white balance, contrast, edge sharpening
 */

#include <Arduino.h>
#include <WiFi.h>
#include "esp_http_server.h"
#include "esp_timer.h"
#include <esp_camera.h>
#include <esp_log.h>
#include <FS.h>
#include <SD_MMC.h>

#include "camera_pins.h"
#include "robot_config.h"
#include "marvin_pins.h"
#include "marvin_motion.h"
#include "marvin_face.h"

// Globals
httpd_handle_t camera_httpd = NULL;
httpd_handle_t stream_httpd = NULL;

bool sdAvailable = false;
bool cameraAvailable = false;
uint32_t captureCounter = 0;

// Function Prototypes
bool initPSRAM();
bool initSDCard();
bool initCamera();
void startCameraServer();
void logMissionEvent(const char* event);

static void marvinLoopTask(void* arg) {
    (void)arg;
    TickType_t lastWake = xTaskGetTickCount();
    for (;;) {
        #if ENABLE_PCA9685
        marvin_motion::tick(20);
        #endif
        #if ENABLE_ST7789
        marvin_face::tick();
        #endif
        vTaskDelayUntil(&lastWake, pdMS_TO_TICKS(20));
    }
}

static String extractJsonStr(const String& body, const char* key) {
    String pat = String("\"") + key + "\"";
    int k = body.indexOf(pat);
    if (k < 0) return "";
    int col = body.indexOf(':', k + pat.length());
    if (col < 0) return "";
    int q1 = body.indexOf('"', col + 1);
    if (q1 < 0) return "";
    int q2 = body.indexOf('"', q1 + 1);
    if (q2 < 0) return "";
    return body.substring(q1 + 1, q2);
}

static bool extractJsonFloat(const String& body, const char* key, float& outVal) {
    String pat = String("\"") + key + "\"";
    int k = body.indexOf(pat);
    if (k < 0) return false;
    int col = body.indexOf(':', k + pat.length());
    if (col < 0) return false;
    int start = col + 1;
    while (start < (int)body.length() && (body[start] == ' ' || body[start] == '\t')) start++;
    if (start >= (int)body.length()) return false;
    char c = body[start];
    if ((c >= '0' && c <= '9') || c == '-' || c == '.') {
        outVal = body.substring(start).toFloat();
        return true;
    }
    return false;
}

// MJPEG Stream constants
#define PART_BOUNDARY "123456789000000000000987654321"
static const char* _STREAM_CONTENT_TYPE = "multipart/x-mixed-replace;boundary=" PART_BOUNDARY;
static const char* _STREAM_BOUNDARY = "\r\n--" PART_BOUNDARY "\r\n";
static const char* _STREAM_PART = "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n";

void setup() {
    Serial.begin(115200);
    delay(500);

    // Suppress camera frame-drop warnings when stream is idle
    esp_log_level_set("cam_hal", ESP_LOG_ERROR);

    Serial.println("\n==========================================");
    Serial.printf("  %s — AI Robot Edge Controller\n", ROBOT_NAME);
    Serial.println("==========================================");

    // 1. Verify PSRAM
    initPSRAM();

    // 2. Initialize Camera
    cameraAvailable = initCamera();

    // 3. Initialize SD Card (only if ENABLE_SDMMC == 1; 0 frees GPIO 38-40 for I2S audio)
    #if ENABLE_SDMMC
    sdAvailable = initSDCard();
    if (sdAvailable) {
        logMissionEvent("SYSTEM_BOOT: SD card mounted successfully.");
    }
    #else
    sdAvailable = false;
    Serial.println("[Storage] SDMMC disabled (GPIO 38/39/40 reserved for I2S audio).");
    #endif

    // 3b. Initialize Marvin 10-Servo PCA9685 & ST7789 GLOOM-MO Visor Display
    #if ENABLE_PCA9685
    marvin_motion::init();
    #endif
    #if ENABLE_ST7789
    marvin_face::init();
    #endif
    xTaskCreatePinnedToCore(marvinLoopTask, "marvin_loop", 4096, nullptr, 1, nullptr, 1);

    // 4. Test snapshot to SD Card
    if (cameraAvailable && sdAvailable) {
        Serial.println("[Boot Test] Capturing startup snapshot to SD card...");
        camera_fb_t *fb = esp_camera_fb_get();
        if (fb) {
            String bootPath = String(PHOTO_DIR) + "/boot.jpg";
            File file = SD_MMC.open(bootPath.c_str(), FILE_WRITE);
            if (file) {
                file.write(fb->buf, fb->len);
                file.close();
                Serial.printf("[Boot Test] Saved startup snapshot -> %s (%u bytes)\n", bootPath.c_str(), fb->len);
                logMissionEvent("BOOT_PHOTO_SAVED");
            } else {
                Serial.println("[Boot Test] Failed to write file to SD.");
            }
            esp_camera_fb_return(fb);
        }
    }

    // 5. Connect to Wi-Fi
    Serial.print("[Network] Connecting to Wi-Fi SSID: ");
    Serial.println(WIFI_SSID);

    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false); // Disable Wi-Fi modem sleep to prevent DMA starvation
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    int attempts = 0;
    while (WiFi.status() != WL_CONNECTED && attempts < 12) {
        delay(500);
        Serial.print(".");
        attempts++;
    }
    Serial.println();

    if (WiFi.status() == WL_CONNECTED) {
        Serial.printf("[Network] Connected! IP Address: %s\n", WiFi.localIP().toString().c_str());
        Serial.printf("[Network] Web Console: http://%s/\n", WiFi.localIP().toString().c_str());
        Serial.printf("[Network] Stream Port: http://%s:81/stream\n", WiFi.localIP().toString().c_str());
    } else {
        Serial.println("[Network] Wi-Fi connection timed out. Starting fallback Access Point...");
        WiFi.mode(WIFI_AP);
        WiFi.softAP(AP_SSID, AP_PASSWORD);
        Serial.printf("[Network] AP '%s' active at http://%s/\n", AP_SSID, WiFi.softAPIP().toString().c_str());
    }

    // 6. Start Asynchronous Non-Blocking Web Server (Port 80 & Port 81)
    startCameraServer();
    Serial.println("[Server] Asynchronous Non-Blocking HTTP & Stream Engine active.");
    Serial.println("==========================================\n");
}

void loop() {
    // Non-blocking loop: esp_http_server runs asynchronously in its own FreeRTOS tasks!
    // Serial console commands
    if (Serial.available()) {
        String cmd = Serial.readStringUntil('\n');
        cmd.trim();
        if (cmd == "capture") {
            char filename[64];
            snprintf(filename, sizeof(filename), "%s/photo_%lu.jpg", PHOTO_DIR, millis() / 1000);
            camera_fb_t *fb = esp_camera_fb_get();
            if (fb && sdAvailable) {
                File file = SD_MMC.open(filename, FILE_WRITE);
                if (file) {
                    file.write(fb->buf, fb->len);
                    file.close();
                    Serial.printf("[Console] Photo saved -> %s (%u bytes)\n", filename, fb->len);
                }
                esp_camera_fb_return(fb);
            }
        } else if (cmd == "sdinit") {
            Serial.println("[Console] Re-initializing SD Card...");
            sdAvailable = initSDCard();
        } else if (cmd == "status") {
            Serial.printf("Free Heap: %lu bytes | Free PSRAM: %lu bytes | Uptime: %lu s | SD: %s\n",
                          (unsigned long)ESP.getFreeHeap(), (unsigned long)ESP.getFreePsram(),
                          millis() / 1000, sdAvailable ? "Mounted" : "Not mounted");
        } else if (cmd == "help") {
            Serial.println("Available serial commands: 'capture', 'sdinit', 'status', 'help'");
        }
    }
    delay(100);
}

// ----------------------------------------------------
// Hardware Initializations
// ----------------------------------------------------

bool initPSRAM() {
    if (psramInit()) {
        Serial.printf("[PSRAM] Initialized successfully. Total PSRAM: %lu MB | Free: %lu MB\n",
                      (unsigned long)ESP.getPsramSize() / (1024 * 1024),
                      (unsigned long)ESP.getFreePsram() / (1024 * 1024));
        return true;
    } else {
        Serial.println("[PSRAM] WARNING: PSRAM not initialized or not found!");
        return false;
    }
}

bool initSDCard() {
    Serial.println("[Storage] Initializing MicroSD card (1-bit SDMMC)...");

    pinMode(SDMMC_CMD_PIN, INPUT_PULLUP);
    pinMode(SDMMC_D0_PIN, INPUT_PULLUP);
    delay(10);
    
    if (!SD_MMC.setPins(SDMMC_CLK_PIN, SDMMC_CMD_PIN, SDMMC_D0_PIN)) {
        Serial.println("[Storage] ERROR: SD_MMC.setPins failed!");
        return false;
    }

    if (!SD_MMC.begin(SD_MOUNT_POINT, true, false, SDMMC_FREQ_DEFAULT, 5)) {
        Serial.println("[Storage] WARNING: SD Card mount failed. Ensure FAT32 formatted card is inserted.");
        return false;
    }

    uint8_t cardType = SD_MMC.cardType();
    if (cardType == CARD_NONE) {
        Serial.println("[Storage] No SD card detected.");
        return false;
    }

    uint64_t cardSize = SD_MMC.cardSize() / (1024 * 1024);
    uint64_t totalBytes = SD_MMC.totalBytes() / (1024 * 1024);
    uint64_t usedBytes = SD_MMC.usedBytes() / (1024 * 1024);

    Serial.printf("[Storage] SD Card Mounted successfully!\n");
    Serial.printf("[Storage] Card Size: %llu MB | Total: %llu MB | Used: %llu MB\n", cardSize, totalBytes, usedBytes);

    if (!SD_MMC.exists(PHOTO_DIR)) {
        SD_MMC.mkdir(PHOTO_DIR);
    }
    return true;
}

bool initCamera() {
    Serial.println("[Camera] Initializing OV2640 sensor...");

    camera_config_t config;
    memset(&config, 0, sizeof(camera_config_t));
    config.ledc_channel = LEDC_CHANNEL_0;
    config.ledc_timer = LEDC_TIMER_0;
    config.pin_d0 = Y2_GPIO_NUM;
    config.pin_d1 = Y3_GPIO_NUM;
    config.pin_d2 = Y4_GPIO_NUM;
    config.pin_d3 = Y5_GPIO_NUM;
    config.pin_d4 = Y6_GPIO_NUM;
    config.pin_d5 = Y7_GPIO_NUM;
    config.pin_d6 = Y8_GPIO_NUM;
    config.pin_d7 = Y9_GPIO_NUM;
    config.pin_xclk = XCLK_GPIO_NUM;
    config.pin_pclk = PCLK_GPIO_NUM;
    config.pin_vsync = VSYNC_GPIO_NUM;
    config.pin_href = HREF_GPIO_NUM;
    config.pin_sccb_sda = SIOD_GPIO_NUM;
    config.pin_sccb_scl = SIOC_GPIO_NUM;
    config.pin_pwdn = PWDN_GPIO_NUM;
    config.pin_reset = RESET_GPIO_NUM;
    config.xclk_freq_hz = 20000000;
    config.pixel_format = PIXFORMAT_JPEG;
    config.frame_size = FRAMESIZE_UXGA; // Pre-allocate buffer for up to UXGA in PSRAM
    config.jpeg_quality = 12;
    config.fb_count = 2;
    config.fb_location = CAMERA_FB_IN_PSRAM;
    config.grab_mode = CAMERA_GRAB_LATEST;

    esp_err_t err = esp_camera_init(&config);
    if (err != ESP_OK) {
        Serial.printf("[Camera] ERROR: esp_camera_init failed with error 0x%x\n", err);
        return false;
    }

    esp_err_t psram_err = esp_camera_set_psram_mode(true);
    Serial.printf("[Camera] PSRAM DMA mode enabled (0x%x)\n", psram_err);

    sensor_t *s = esp_camera_sensor_get();
    if (s != nullptr) {
        Serial.printf("[Camera] Sensor PID: 0x%02x (Ready)\n", s->id.PID);
        s->set_framesize(s, FRAMESIZE_VGA); // Default to VGA for clear responsive streaming
        s->set_vflip(s, 1);
        s->set_hmirror(s, 0);

        // Hardware digital signal processing optimizations for robot vision
        s->set_brightness(s, 1);     // Slight boost for indoor lighting
        s->set_contrast(s, 1);       // Crisper edges
        s->set_saturation(s, 0);
        s->set_whitebal(s, 1);       // Enable Auto White Balance
        s->set_awb_gain(s, 1);
        s->set_wb_mode(s, 0);        // Auto mode
        s->set_exposure_ctrl(s, 1);  // Enable Auto Exposure
        s->set_aec2(s, 1);
        s->set_gain_ctrl(s, 1);      // Enable Auto Gain Control
        s->set_bpc(s, 1);            // Black pixel correction
        s->set_wpc(s, 1);            // White pixel correction
        s->set_raw_gma(s, 1);        // Gamma curve
        s->set_lenc(s, 1);           // Lens correction
    }
    return true;
}

void logMissionEvent(const char* event) {
    if (!sdAvailable) return;
    File log = SD_MMC.open(LOG_FILE, FILE_APPEND);
    if (log) {
        log.printf("[%lu s] %s\n", millis() / 1000, event);
        log.close();
    }
}

// ----------------------------------------------------
// Web Server HTML Console
// ----------------------------------------------------

static const char PROGMEM INDEX_HTML[] = R"rawliteral(
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Rocky-S3 Robot Console</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 20px; }
        .container { max-width: 900px; margin: 0 auto; }
        h1 { color: #38bdf8; display: flex; align-items: center; gap: 10px; margin-bottom: 5px; }
        .badge { background: #0369a1; padding: 4px 10px; border-radius: 6px; font-size: 14px; color: #e0f2fe; }
        .stream-box { background: #1e293b; border-radius: 12px; overflow: hidden; box-shadow: 0 10px 25px rgba(0,0,0,0.5); text-align: center; margin: 20px 0; }
        img.stream { width: 100%; max-height: 520px; object-fit: contain; display: block; background: #000; }
        .controls { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin: 20px 0; }
        button { background: #2563eb; color: #fff; border: none; padding: 12px 18px; border-radius: 8px; font-weight: 600; cursor: pointer; transition: background 0.2s; }
        button:hover { background: #1d4ed8; }
        button.secondary { background: #334155; }
        button.secondary:hover { background: #475569; }
        .stats { background: #1e293b; padding: 16px; border-radius: 8px; font-family: monospace; font-size: 13px; line-height: 1.6; color: #94a3b8; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🤖 Rocky-S3 Robot Console <span class="badge">Async Edge Vision</span></h1>
        <p style="color: #94a3b8; margin-top: 0;">Connected to local AI Brain at ~/rocky-voice</p>
        
        <div class="stream-box">
            <img class="stream" src="/stream" alt="Live Video Stream">
        </div>

        <div class="controls">
            <button onclick="sendGesture('step_forward')">🚶 Waddle</button>
            <button onclick="sendGesture('nod')">🟢 Nod</button>
            <button onclick="sendGesture('shake')">🔴 Shake</button>
            <button onclick="sendGesture('wave')">👋 Wave</button>
            <button onclick="sendGesture('sigh')">😔 Sigh</button>
            <button class="secondary" onclick="sendGesture('home')">🏠 Home</button>
            <button class="secondary" onclick="setMood('bored')">😒 Bored</button>
            <button class="secondary" onclick="setMood('happy')">🙂 Happy</button>
            <button class="secondary" onclick="setMood('surprised')">😮 Surprised</button>
            <button class="secondary" onclick="savePhoto()">📸 Save Photo</button>
            <button class="secondary" onclick="window.open('/capture', '_blank')">🖼️ Frame</button>
            <button class="secondary" onclick="refreshStatus()">📊 Status</button>
        </div>

        <div class="stats" id="stats-panel">Loading telemetry...</div>
    </div>

    <script>
        async function sendGesture(act) {
            await fetch('/gesture?action=' + encodeURIComponent(act), {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({action: act})
            });
        }
        async function setMood(mood) {
            await fetch('/face', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({mood, gx: 0, gy: 0, speaking: false})
            });
        }
        async function setRes(val) {
            await fetch(`/control?var=framesize&val=${val}`);
        }
        async function savePhoto() {
            try {
                const res = await fetch('/save', { method: 'POST' });
                const data = await res.json();
                alert(data.file ? 'Photo captured: ' + data.file : (data.error || 'Saved!'));
            } catch (e) {
                alert('Save failed: ' + e);
            }
        }
        async function refreshStatus() {
            try {
                const res = await fetch('/status');
                const data = await res.json();
                document.getElementById('stats-panel').innerHTML = 
                    `Uptime: ${data.uptime_sec}s | Heap Free: ${(data.free_heap/1024).toFixed(1)} KB | PSRAM Free: ${(data.free_psram/(1024*1024)).toFixed(2)} MB<br>` +
                    `SD Card: ${data.sd_mounted ? 'Mounted (' + (data.sd_total_mb) + ' MB)' : 'Not Available'} | WiFi RSSI: ${data.wifi_rssi} dBm`;
            } catch (e) {
                document.getElementById('stats-panel').innerText = 'Telemetry offline';
            }
        }
        setInterval(refreshStatus, 4000);
        refreshStatus();
    </script>
</body>
</html>
)rawliteral";

// ----------------------------------------------------
// Asynchronous HTTP Handlers (esp_http_server)
// ----------------------------------------------------

static esp_err_t index_handler(httpd_req_t *req) {
    httpd_resp_set_type(req, "text/html");
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    return httpd_resp_send(req, INDEX_HTML, HTTPD_RESP_USE_STRLEN);
}

static esp_err_t capture_handler(httpd_req_t *req) {
    if (!cameraAvailable) {
        httpd_resp_send_500(req);
        return ESP_FAIL;
    }
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) {
        httpd_resp_send_500(req);
        return ESP_FAIL;
    }
    httpd_resp_set_type(req, "image/jpeg");
    httpd_resp_set_hdr(req, "Content-Disposition", "inline; filename=capture.jpg");
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    esp_err_t res = httpd_resp_send(req, (const char *)fb->buf, fb->len);
    esp_camera_fb_return(fb);
    return res;
}

static esp_err_t stream_handler(httpd_req_t *req) {
    camera_fb_t *fb = NULL;
    esp_err_t res = ESP_OK;
    char part_buf[128];

    res = httpd_resp_set_type(req, _STREAM_CONTENT_TYPE);
    if (res != ESP_OK) return res;
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    httpd_resp_set_hdr(req, "X-Framerate", "30");

    while (true) {
        fb = esp_camera_fb_get();
        if (!fb) {
            vTaskDelay(pdMS_TO_TICKS(10));
            continue;
        }

        size_t hlen = snprintf(part_buf, sizeof(part_buf), _STREAM_PART, fb->len);
        res = httpd_resp_send_chunk(req, (const char *)part_buf, hlen);
        if (res == ESP_OK) {
            res = httpd_resp_send_chunk(req, (const char *)fb->buf, fb->len);
        }
        if (res == ESP_OK) {
            res = httpd_resp_send_chunk(req, _STREAM_BOUNDARY, strlen(_STREAM_BOUNDARY));
        }
        esp_camera_fb_return(fb);
        fb = NULL;

        if (res != ESP_OK) break;
        vTaskDelay(pdMS_TO_TICKS(15)); // Yield cleanly to FreeRTOS
    }
    return res;
}

static esp_err_t status_handler(httpd_req_t *req) {
    char json[320];
    snprintf(json, sizeof(json),
        "{\"robot\":\"%s\",\"uptime_sec\":%lu,\"free_heap\":%lu,\"free_psram\":%lu,"
        "\"sd_mounted\":%s,\"sd_total_mb\":%llu,\"wifi_rssi\":%d}",
        ROBOT_NAME,
        millis() / 1000,
        (unsigned long)ESP.getFreeHeap(),
        (unsigned long)ESP.getFreePsram(),
        sdAvailable ? "true" : "false",
        sdAvailable ? (SD_MMC.totalBytes() / (1024 * 1024)) : 0,
        WiFi.RSSI()
    );
    httpd_resp_set_type(req, "application/json");
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    return httpd_resp_send(req, json, strlen(json));
}

static esp_err_t save_handler(httpd_req_t *req) {
    httpd_resp_set_type(req, "application/json");
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");

    if (!cameraAvailable || !sdAvailable) {
        return httpd_resp_send(req, "{\"error\":\"Camera or SD card not available\"}", HTTPD_RESP_USE_STRLEN);
    }
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) {
        return httpd_resp_send(req, "{\"error\":\"Capture failed\"}", HTTPD_RESP_USE_STRLEN);
    }

    captureCounter++;
    char filename[64];
    snprintf(filename, sizeof(filename), "%s/photo_%04u.jpg", PHOTO_DIR, captureCounter);

    File file = SD_MMC.open(filename, FILE_WRITE);
    if (!file) {
        esp_camera_fb_return(fb);
        return httpd_resp_send(req, "{\"error\":\"Could not open file on SD\"}", HTTPD_RESP_USE_STRLEN);
    }

    file.write(fb->buf, fb->len);
    file.close();
    uint32_t bytesWritten = fb->len;
    esp_camera_fb_return(fb);

    char eventMsg[128];
    snprintf(eventMsg, sizeof(eventMsg), "PHOTO_CAPTURED: %s (%u bytes)", filename, bytesWritten);
    logMissionEvent(eventMsg);

    char jsonBuf[160];
    snprintf(jsonBuf, sizeof(jsonBuf), "{\"status\":\"ok\",\"file\":\"%s\",\"bytes\":%u}", filename, bytesWritten);
    return httpd_resp_send(req, jsonBuf, strlen(jsonBuf));
}

static esp_err_t control_handler(httpd_req_t *req) {
    char* buf;
    size_t buf_len = httpd_req_get_url_query_len(req) + 1;
    if (buf_len > 1) {
        buf = (char*)malloc(buf_len);
        if (httpd_req_get_url_query_str(req, buf, buf_len) == ESP_OK) {
            char variable[32] = {0,};
            char value[32] = {0,};
            if (httpd_query_key_value(buf, "var", variable, sizeof(variable)) == ESP_OK &&
                httpd_query_key_value(buf, "val", value, sizeof(value)) == ESP_OK) {
                int val = atoi(value);
                sensor_t *s = esp_camera_sensor_get();
                int res = 0;
                if (!strcmp(variable, "framesize")) {
                    res = s->set_framesize(s, (framesize_t)val);
                } else if (!strcmp(variable, "quality")) {
                    res = s->set_quality(s, val);
                } else if (!strcmp(variable, "contrast")) {
                    res = s->set_contrast(s, val);
                } else if (!strcmp(variable, "brightness")) {
                    res = s->set_brightness(s, val);
                } else if (!strcmp(variable, "saturation")) {
                    res = s->set_saturation(s, val);
                } else if (!strcmp(variable, "vflip")) {
                    res = s->set_vflip(s, val);
                } else if (!strcmp(variable, "hmirror")) {
                    res = s->set_hmirror(s, val);
                }
                free(buf);
                httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
                if (res != 0) {
                    httpd_resp_send_500(req);
                    return ESP_FAIL;
                }
                return httpd_resp_send(req, "{\"status\":\"ok\"}", HTTPD_RESP_USE_STRLEN);
            }
        }
        free(buf);
    }
    httpd_resp_send_404(req);
    return ESP_FAIL;
}

static esp_err_t action_handler(httpd_req_t *req) {
    char content[256] = {0};
    if (req->content_len > 0) {
        size_t recv_size = min(req->content_len, sizeof(content) - 1);
        int ret = httpd_req_recv(req, content, recv_size);
        if (ret < 0) {
            if (ret == HTTPD_SOCK_ERR_TIMEOUT) httpd_resp_send_408(req);
            return ESP_FAIL;
        }
        content[ret] = '\0';
    }

    String body(content);
    String action = extractJsonStr(body, "action");
    if (action.isEmpty()) {
        // Also support ?action= query param (used by tars_gestures.py)
        char qbuf[96] = {0};
        if (httpd_req_get_url_query_str(req, qbuf, sizeof(qbuf)) == ESP_OK) {
            char aval[48] = {0};
            if (httpd_query_key_value(qbuf, "action", aval, sizeof(aval)) == ESP_OK) {
                action = String(aval);
            }
        }
    }

    float dur = 2200.0f;
    extractJsonFloat(body, "duration_ms", dur);
    if (!action.isEmpty()) {
        Serial.printf("[Action] Executing '%s' (%lu ms)\n", action.c_str(), (unsigned long)dur);
        marvin_motion::triggerAction(action, (unsigned long)dur);
        if (action == "sigh" || action == "crouch" || action == "brood") {
            marvin_face::setState("sad", 0.0f, -0.3f, false);
        } else if (action == "wave" || action == "fist_bump") {
            marvin_face::setState("happy", 0.0f, 0.0f, false);
        }
    }
    logMissionEvent(content);

    httpd_resp_set_type(req, "application/json");
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    return httpd_resp_send(req, "{\"status\":\"executed\"}", HTTPD_RESP_USE_STRLEN);
}

static esp_err_t servo_handler(httpd_req_t *req) {
    char content[256] = {0};
    size_t recv_size = min(req->content_len, sizeof(content) - 1);
    int ret = httpd_req_recv(req, content, recv_size);
    if (ret <= 0) {
        if (ret == HTTPD_SOCK_ERR_TIMEOUT) httpd_resp_send_408(req);
        return ESP_FAIL;
    }
    content[ret] = '\0';
    String body(content);

    float pan = 0, tilt = 0, ch = -1, angle = 90, speed = 220;
    extractJsonFloat(body, "speed", speed);
    if (extractJsonFloat(body, "ch", ch) && extractJsonFloat(body, "angle", angle)) {
        if (ch >= 0 && ch < MARVIN_SERVO_COUNT) {
            marvin_motion::setTarget((uint8_t)ch, angle, speed);
        }
    }
    if (extractJsonFloat(body, "pan", pan)) {
        marvin_motion::setTarget(CH_HEAD_PAN, pan, speed);
    }
    if (extractJsonFloat(body, "tilt", tilt)) {
        marvin_motion::setTarget(CH_HEAD_TILT, tilt, speed);
    }

    httpd_resp_set_type(req, "application/json");
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    return httpd_resp_send(req, "{\"status\":\"ok\"}", HTTPD_RESP_USE_STRLEN);
}

static esp_err_t face_handler(httpd_req_t *req) {
    char content[256] = {0};
    size_t recv_size = min(req->content_len, sizeof(content) - 1);
    int ret = httpd_req_recv(req, content, recv_size);
    if (ret <= 0) {
        if (ret == HTTPD_SOCK_ERR_TIMEOUT) httpd_resp_send_408(req);
        return ESP_FAIL;
    }
    content[ret] = '\0';
    String body(content);

    String mood = extractJsonStr(body, "mood");
    float gx = marvin_face::gazeX;
    float gy = marvin_face::gazeY;
    extractJsonFloat(body, "gx", gx);
    extractJsonFloat(body, "gy", gy);
    bool speaking = (body.indexOf("\"speaking\":true") >= 0 || body.indexOf("\"speaking\": true") >= 0);
    marvin_face::setState(mood, gx, gy, speaking);

    httpd_resp_set_type(req, "application/json");
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    return httpd_resp_send(req, "{\"status\":\"ok\"}", HTTPD_RESP_USE_STRLEN);
}

void startCameraServer() {
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.server_port = 80;
    config.ctrl_port = 32768;
    config.max_uri_handlers = 14;
    config.max_open_sockets = 7;
    config.backlog_conn = 5;
    config.stack_size = 8192;
    config.lru_purge_enable = true;

    httpd_uri_t index_uri = { .uri = "/", .method = HTTP_GET, .handler = index_handler, .user_ctx = NULL };
    httpd_uri_t status_uri = { .uri = "/status", .method = HTTP_GET, .handler = status_handler, .user_ctx = NULL };
    httpd_uri_t capture_uri = { .uri = "/capture", .method = HTTP_GET, .handler = capture_handler, .user_ctx = NULL };
    httpd_uri_t save_uri = { .uri = "/save", .method = HTTP_POST, .handler = save_handler, .user_ctx = NULL };
    httpd_uri_t control_uri = { .uri = "/control", .method = HTTP_GET, .handler = control_handler, .user_ctx = NULL };
    httpd_uri_t action_uri = { .uri = "/action", .method = HTTP_POST, .handler = action_handler, .user_ctx = NULL };
    httpd_uri_t gesture_uri = { .uri = "/gesture", .method = HTTP_POST, .handler = action_handler, .user_ctx = NULL };
    httpd_uri_t servo_uri = { .uri = "/servo", .method = HTTP_POST, .handler = servo_handler, .user_ctx = NULL };
    httpd_uri_t face_uri = { .uri = "/face", .method = HTTP_POST, .handler = face_handler, .user_ctx = NULL };
    httpd_uri_t stream_uri = { .uri = "/stream", .method = HTTP_GET, .handler = stream_handler, .user_ctx = NULL };

    if (httpd_start(&camera_httpd, &config) == ESP_OK) {
        httpd_register_uri_handler(camera_httpd, &index_uri);
        httpd_register_uri_handler(camera_httpd, &status_uri);
        httpd_register_uri_handler(camera_httpd, &capture_uri);
        httpd_register_uri_handler(camera_httpd, &save_uri);
        httpd_register_uri_handler(camera_httpd, &control_uri);
        httpd_register_uri_handler(camera_httpd, &action_uri);
        httpd_register_uri_handler(camera_httpd, &gesture_uri);
        httpd_register_uri_handler(camera_httpd, &servo_uri);
        httpd_register_uri_handler(camera_httpd, &face_uri);
        Serial.println("[Server] Command, Servo, Face & Telemetry Server active on Port 80.");
    }

    // High-throughput isolated stream server on Port 81 (eliminates socket starvation on Port 80)
    httpd_config_t stream_config = HTTPD_DEFAULT_CONFIG();
    stream_config.server_port = 81;
    stream_config.ctrl_port = 32769;
    stream_config.max_uri_handlers = 4;
    stream_config.max_open_sockets = 4;
    stream_config.backlog_conn = 3;
    stream_config.stack_size = 8192;
    stream_config.lru_purge_enable = true;

    if (httpd_start(&stream_httpd, &stream_config) == ESP_OK) {
        httpd_register_uri_handler(stream_httpd, &stream_uri);
        Serial.println("[Server] Isolated Video Stream Server active on Port 81.");
    }
}
