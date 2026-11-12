#pragma once

// Robot Identity & Mode
#define ROBOT_NAME          "Marvin-S3"
#define ROBOT_ROLE          "Bipedal Paranoid Android Assistant"

// Hardware Subsystem Toggles
#define ENABLE_SDMMC        0   // 0 = frees GPIO 38/39/40 for I2S audio (compact S3 CAM has no SD)
#define ENABLE_PCA9685      1   // 10-DOF servos via I2C PCA9685
#define ENABLE_ST7789       1   // 2.0" 320x240 IPS LCD visor face

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
