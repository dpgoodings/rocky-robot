#pragma once

// Camera Pin Mapping for Freenove ESP32-S3 WROOM CAM / ESP32S3-EYE compatible
// Sensor: OV2640 / OV3660 / OV5640

#define PWDN_GPIO_NUM     -1
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM     15
#define SIOD_GPIO_NUM      4
#define SIOC_GPIO_NUM      5

#define Y9_GPIO_NUM       16
#define Y8_GPIO_NUM       17
#define Y7_GPIO_NUM       18
#define Y6_GPIO_NUM       12
#define Y5_GPIO_NUM       10
#define Y4_GPIO_NUM        8
#define Y3_GPIO_NUM        9
#define Y2_GPIO_NUM       11
#define VSYNC_GPIO_NUM     6
#define HREF_GPIO_NUM      7
#define PCLK_GPIO_NUM     13

// Onboard MicroSD Card Pinout (SDMMC 1-bit mode)
#define SDMMC_CMD_PIN     38
#define SDMMC_CLK_PIN     39
#define SDMMC_D0_PIN      40
