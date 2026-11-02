#pragma once

// Robot Identity & Mode
#define ROBOT_NAME          "Rocky-S3"
#define ROBOT_ROLE          "Autonomous Exploration Scout"

// Wi-Fi Configuration
#define WIFI_SSID           "Heaven"
#define WIFI_PASSWORD       "@nnA.001"
#define AP_SSID             "Rocky-Robot-AP"
#define AP_PASSWORD         "rocky1234"

// Rocky-Voice AI Brain Server (Running on your Mac at ~/rocky-voice)
#define ROCKY_BRAIN_HOST    "http://192.168.4.135:7860"

// Camera Capture Settings
#define DEFAULT_FRAME_SIZE  FRAMESIZE_VGA    // 640x480 (Good balance of speed and clarity)
#define DEFAULT_JPEG_QUAL   12               // 10-63 (Lower = higher quality, 12 is sharp and fast)
#define FB_COUNT            2                // Frame buffer count in PSRAM (2 enables double buffering)

// SD Storage Paths
#define SD_MOUNT_POINT      "/sdcard"
#define PHOTO_DIR           "/sdcard/captures"
#define LOG_FILE            "/sdcard/mission.log"
