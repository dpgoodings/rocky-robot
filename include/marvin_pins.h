#pragma once

// Marvin-S3 Peripheral Pin Assignments for 44-pin Dual-USB-C ESP32-S3-WROOM CAM (N16R8)
// Conflict-free with Camera (GPIO 4-18), Octal PSRAM (GPIO 35-37), and WS2812 (GPIO 48).

// PCA9685 16-Channel PWM Servo Driver (I2C @ 0x40, VCC=3.3V, V+=5V UBEC)
#define MARVIN_I2C_SDA       1
#define MARVIN_I2C_SCL       2
#define PCA9685_I2C_ADDR     0x40

// Waveshare 2.0" IPS LCD (320x240 ST7789 SPI, VCC=3.3V, BL=3.3V)
#define LCD_PIN_MOSI         47
#define LCD_PIN_SCLK         21
#define LCD_PIN_CS           14
#define LCD_PIN_DC           41
#define LCD_PIN_RST          42
#define LCD_WIDTH            320
#define LCD_HEIGHT           240

// Shared Full-Duplex I2S0 Audio (INMP441 Mic + MAX98357A Speaker Amp)
// Requires ENABLE_SDMMC == 0 because GPIO 38/39/40 are shared with SDMMC.
#define I2S_PIN_BCLK         38
#define I2S_PIN_LRC          39
#define I2S_PIN_MIC_SD       3
#define I2S_PIN_AMP_DIN      40

// PCA9685 Servo Channel Assignments (CH0 .. CH9)
#define CH_HEAD_PAN          0   // Yaw:   15..165 deg (90 = center)
#define CH_HEAD_TILT         1   // Pitch: 58..132 deg (90 = level, >90 = up)
#define CH_L_SHOULDER_PITCH  2   // Left arm forward/back
#define CH_L_SHOULDER_ROLL   3   // Left arm raise/lower
#define CH_R_SHOULDER_PITCH  4   // Right arm forward/back
#define CH_R_SHOULDER_ROLL   5   // Right arm raise/lower
#define CH_L_HIP_PITCH       6   // Left leg swing
#define CH_L_ANKLE_ROLL      7   // Left boot waddle lean
#define CH_R_HIP_PITCH       8   // Right leg swing
#define CH_R_ANKLE_ROLL      9   // Right boot waddle lean
#define MARVIN_SERVO_COUNT   10
