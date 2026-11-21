#pragma once

#include <Arduino.h>
#include <SPI.h>
#include <math.h>
#include <algorithm>
#include "esp_heap_caps.h"
#include "marvin_pins.h"

#ifndef LCD_PIN_BL
#define LCD_PIN_BL 45
#endif

namespace marvin_face {

// =========================================================================
// RGB565 Palette (Byte-swapped for direct Big-Endian SPI DMA streaming)
// =========================================================================
#define RGB565_SWAP(r, g, b) ((uint16_t)(((((r) & 0xF8) | ((g) >> 5)) << 8) | ((((g) & 0x1C) << 3) | ((b) >> 3))))
#define TO_BE(c) ((uint16_t)(((uint16_t)(c) >> 8) | ((uint16_t)(c) << 8)))

static const uint16_t COL_BG         = TO_BE(0x0862);  // Deep Charcoal Visor (#080c12)
static const uint16_t COL_GLOW       = TO_BE(0xB6FA);  // Mint-Cyan GLOOM-MO (#b7dcd4)
static const uint16_t COL_GLOW_DIM   = TO_BE(0x31E6);  // Subtle frame (#303d4c)
static const uint16_t COL_HAPPY      = TO_BE(0x36B3);  // Mint-Emerald (#34d399)
static const uint16_t COL_WARN       = TO_BE(0xFBE4);  // Amber (#f59e0b)
static const uint16_t COL_SAD        = TO_BE(0x6479);  // Slate-Blue (#5b7b99)
static const uint16_t COL_WHITE      = TO_BE(0xFFFF);
static const uint16_t COL_PUPIL      = TO_BE(0x0000);
static const uint16_t COL_RED        = TO_BE(0xF800);
static const uint16_t COL_GREEN      = TO_BE(0x07E0);
static const uint16_t COL_BLUE       = TO_BE(0x001F);
static const uint16_t COL_YELLOW     = TO_BE(0xFFE0);
static const uint16_t COL_MAGENTA    = TO_BE(0xF81F);
static const uint16_t COL_CYAN       = TO_BE(0x07FF);
static const uint16_t COL_ORANGE     = TO_BE(0xFD20);

// BMO Face Palette (Adventure Time Mint CRT)
static const uint16_t COL_BMO_BG       = TO_BE(0xCF7C);  // Mint CRT Screen (#cdeee0)
static const uint16_t COL_BMO_BORDER   = TO_BE(0xAD58);  // Bezel border (#aad7c6)
static const uint16_t COL_BMO_INK      = TO_BE(0x1144);  // Deep Forest Ink (#142826)
static const uint16_t COL_BMO_BLUSH    = TO_BE(0xE5D7);  // Soft Blended Cheek Blush (#e2b9be)
static const uint16_t COL_BMO_MOUTH_BG = TO_BE(0x1A67);  // Open Mouth Dark (#1b4d3e)
static const uint16_t COL_BMO_TONGUE   = TO_BE(0xF473);  // Coral Pink Tongue (#f08c9e)
static const uint16_t COL_BMO_SWEAT    = TO_BE(0x3DFE);  // Tear/Sweat Cyan (#38bdf8)

enum FaceStyle {
    STYLE_BMO = 0,
    STYLE_ROBOEYES = 1
};
static volatile FaceStyle currentStyle = STYLE_BMO;

// Active Face State
static SemaphoreHandle_t faceMutex = nullptr;
static volatile float gazeX = 0.0f;
static volatile float gazeY = 0.0f;
static volatile bool isSpeaking = false;
static volatile bool needsFullWipe = true;
static volatile bool needsRedraw = true;

static String displayMsg = "HELLO";
static volatile bool msgMode = true;

// =========================================================================
// Hardware SPI Engine for Sitronix ST7789 320x240 IPS LCD (HSPI @ 40MHz)
// =========================================================================
static SPIClass* spiLcd = nullptr;
static const SPISettings spiSettings(40000000, MSBFIRST, SPI_MODE0);

static inline void writeCmd(uint8_t c) {
    digitalWrite(LCD_PIN_DC, LOW);
    digitalWrite(LCD_PIN_CS, LOW);
    spiLcd->beginTransaction(spiSettings);
    spiLcd->transfer(c);
    spiLcd->endTransaction();
    digitalWrite(LCD_PIN_CS, HIGH);
}

static inline void writeData(uint8_t d) {
    digitalWrite(LCD_PIN_DC, HIGH);
    digitalWrite(LCD_PIN_CS, LOW);
    spiLcd->beginTransaction(spiSettings);
    spiLcd->transfer(d);
    spiLcd->endTransaction();
    digitalWrite(LCD_PIN_CS, HIGH);
}

static void setWindow(uint16_t x0, uint16_t y0, uint16_t x1, uint16_t y1) {
    writeCmd(0x2A);
    writeData(x0 >> 8); writeData(x0 & 0xFF);
    writeData(x1 >> 8); writeData(x1 & 0xFF);
    writeCmd(0x2B);
    writeData(y0 >> 8); writeData(y0 & 0xFF);
    writeData(y1 >> 8); writeData(y1 & 0xFF);
    writeCmd(0x2C);
}

// =========================================================================
// PSRAM Offscreen Framebuffer (Zero-Flicker Double Buffering)
// =========================================================================
static uint16_t* frameBuffer = nullptr;

static void bufFillRect(int16_t x, int16_t y, int16_t w, int16_t h, uint16_t color) {
    if (!frameBuffer) return;
    if (x < 0) { w += x; x = 0; }
    if (y < 0) { h += y; y = 0; }
    if (x + w > LCD_WIDTH) w = LCD_WIDTH - x;
    if (y + h > LCD_HEIGHT) h = LCD_HEIGHT - y;
    if (w <= 0 || h <= 0) return;

    for (int16_t r = 0; r < h; r++) {
        uint16_t* row = &frameBuffer[(y + r) * LCD_WIDTH + x];
        for (int16_t c = 0; c < w; c++) {
            row[c] = color;
        }
    }
}

static void bufFillRoundRect(int16_t x, int16_t y, int16_t w, int16_t h, int16_t r, uint16_t color) {
    if (w <= 0 || h <= 0) return;
    if (r <= 0) { bufFillRect(x, y, w, h, color); return; }
    if (r > w / 2) r = w / 2;
    if (r > h / 2) r = h / 2;

    // Center body
    bufFillRect(x, y + r, w, h - 2 * r, color);

    // Rounded corners
    for (int16_t dy = 0; dy < r; dy++) {
        int16_t d = r - dy;
        int16_t cornerOffset = r - (int16_t)sqrtf(max(0.0f, (float)(r * r - d * d)));
        int16_t lineW = w - 2 * cornerOffset;
        if (lineW > 0) {
            bufFillRect(x + cornerOffset, y + dy, lineW, 1, color);                  // Top row
            bufFillRect(x + cornerOffset, y + h - 1 - dy, lineW, 1, color);          // Bottom row
        }
    }
}

static void bufFillTriangle(int16_t x0, int16_t y0, int16_t x1, int16_t y1, int16_t x2, int16_t y2, uint16_t color) {
    if (y0 > y1) { std::swap(y0, y1); std::swap(x0, x1); }
    if (y1 > y2) { std::swap(y1, y2); std::swap(x1, x2); }
    if (y0 > y1) { std::swap(y0, y1); std::swap(x0, x1); }

    if (y0 == y2) {
        int16_t a = min({x0, x1, x2});
        int16_t b = max({x0, x1, x2});
        bufFillRect(a, y0, b - a + 1, 1, color);
        return;
    }

    int16_t dx01 = x1 - x0, dy01 = y1 - y0;
    int16_t dx02 = x2 - x0, dy02 = y2 - y0;
    int16_t dx12 = x2 - x1, dy12 = y2 - y1;
    int32_t sa = 0, sb = 0;
    int16_t last = (y1 == y2) ? y1 : y1 - 1;

    for (int16_t y = y0; y <= last; y++) {
        int16_t a = (dy01 > 0) ? (x0 + sa / dy01) : x0;
        int16_t b = (dy02 > 0) ? (x0 + sb / dy02) : x0;
        sa += dx01;
        sb += dx02;
        if (a > b) std::swap(a, b);
        bufFillRect(a, y, b - a + 1, 1, color);
    }

    sa = (int32_t)dx12 * (last + 1 - y1);
    sb = (int32_t)dx02 * (last + 1 - y0);
    for (int16_t y = last + 1; y <= y2; y++) {
        int16_t a = (dy12 > 0) ? (x1 + sa / dy12) : x1;
        int16_t b = (dy02 > 0) ? (x0 + sb / dy02) : x0;
        sa += dx12;
        sb += dx02;
        if (a > b) std::swap(a, b);
        bufFillRect(a, y, b - a + 1, 1, color);
    }
}

static void bufFillEllipse(int16_t cx, int16_t cy, int16_t rx, int16_t ry, uint16_t color) {
    if (!frameBuffer || rx <= 0 || ry <= 0) return;
    int16_t y0 = max((int16_t)0, (int16_t)(cy - ry));
    int16_t y1 = min((int16_t)(LCD_HEIGHT - 1), (int16_t)(cy + ry));
    float ry2 = (float)(ry * ry);

    for (int16_t y = y0; y <= y1; y++) {
        int16_t dy = y - cy;
        float factor = 1.0f - (float)(dy * dy) / ry2;
        if (factor < 0.0f) continue;
        int16_t dx = (int16_t)(rx * sqrtf(factor));
        int16_t x0 = max((int16_t)0, (int16_t)(cx - dx));
        int16_t x1 = min((int16_t)(LCD_WIDTH - 1), (int16_t)(cx + dx));
        int16_t len = x1 - x0 + 1;
        if (len > 0) {
            uint16_t* row = &frameBuffer[y * LCD_WIDTH + x0];
            for (int16_t i = 0; i < len; i++) row[i] = color;
        }
    }
}

static void bufDrawThickLine(int16_t x0, int16_t y0, int16_t x1, int16_t y1, int16_t thickness, uint16_t color) {
    if (!frameBuffer) return;
    int16_t dx = abs(x1 - x0), sx = x0 < x1 ? 1 : -1;
    int16_t dy = -abs(y1 - y0), sy = y0 < y1 ? 1 : -1;
    int16_t err = dx + dy, e2;
    int16_t rad = max(1, thickness / 2);

    while (true) {
        bufFillEllipse(x0, y0, rad, rad, color);
        if (x0 == x1 && y0 == y1) break;
        e2 = 2 * err;
        if (e2 >= dy) { err += dy; x0 += sx; }
        if (e2 <= dx) { err += dx; y0 += sy; }
    }
}

static void bufDrawHappyArc(int16_t cx, int16_t cy, float scale = 1.35f) {
    // Upward crescent ^ arc matching index.html ctx.arc(0, 4, 10, PI * 1.12, PI * 1.88)
    int steps = 16;
    float aStart = 1.12f * 3.14159265f;
    float aEnd   = 1.88f * 3.14159265f;
    float r = 10.0f * scale;
    int16_t yCenter = cy + (int16_t)(4.0f * scale);
    int16_t prevX = cx + (int16_t)(r * cosf(aStart));
    int16_t prevY = yCenter + (int16_t)(r * sinf(aStart));

    for (int i = 1; i <= steps; i++) {
        float t = (float)i / (float)steps;
        float a = aStart + t * (aEnd - aStart);
        int16_t curX = cx + (int16_t)(r * cosf(a));
        int16_t curY = yCenter + (int16_t)(r * sinf(a));
        bufDrawThickLine(prevX, prevY, curX, curY, 4, COL_BMO_INK);
        prevX = curX;
        prevY = curY;
    }
}

static void bufDrawSleepyArc(int16_t cx, int16_t cy, float scale = 1.35f) {
    // Downward sleepy curve matching index.html ctx.arc(0, -3, 8.5, PI * 0.15, PI * 0.85)
    int steps = 16;
    float aStart = 0.15f * 3.14159265f;
    float aEnd   = 0.85f * 3.14159265f;
    float r = 8.5f * scale;
    int16_t yCenter = cy - (int16_t)(3.0f * scale);
    int16_t prevX = cx + (int16_t)(r * cosf(aStart));
    int16_t prevY = yCenter + (int16_t)(r * sinf(aStart));

    for (int i = 1; i <= steps; i++) {
        float t = (float)i / (float)steps;
        float a = aStart + t * (aEnd - aStart);
        int16_t curX = cx + (int16_t)(r * cosf(a));
        int16_t curY = yCenter + (int16_t)(r * sinf(a));
        bufDrawThickLine(prevX, prevY, curX, curY, 4, COL_BMO_INK);
        prevX = curX;
        prevY = curY;
    }
}

static void bufDrawBmoSmile(int16_t cx, int16_t cy, float smile, float scale = 1.35f) {
    // Quadratic bezier curve matching index.html
    float halfW = 15.0f * scale;
    float curve = smile * 11.0f * scale;
    float x0 = -halfW * 0.85f;
    float y0 = -curve * 0.18f;
    float x1 = 0.0f;
    float y1 = curve;
    float x2 = halfW * 0.85f;
    float y2 = -curve * 0.18f;

    int steps = 16;
    int16_t prevX = cx + (int16_t)x0;
    int16_t prevY = cy + (int16_t)y0;

    for (int i = 1; i <= steps; i++) {
        float t = (float)i / (float)steps;
        float xt = (1.0f - t) * (1.0f - t) * x0 + 2.0f * (1.0f - t) * t * x1 + t * t * x2;
        float yt = (1.0f - t) * (1.0f - t) * y0 + 2.0f * (1.0f - t) * t * y1 + t * t * y2;
        int16_t curX = cx + (int16_t)xt;
        int16_t curY = cy + (int16_t)yt;
        bufDrawThickLine(prevX, prevY, curX, curY, 4, COL_BMO_INK);
        prevX = curX;
        prevY = curY;
    }
}

static void bufDrawBmoMouthOpen(int16_t cx, int16_t cy, float open, float smile, float scale = 1.35f) {
    if (open < 0.08f) {
        bufDrawBmoSmile(cx, cy, smile, scale);
        return;
    }

    float halfW = (16.0f + open * 5.0f) * scale;
    float openH = (8.0f + open * 22.0f) * scale;
    int16_t topY = cy - (int16_t)(openH * 0.22f);
    int16_t botY = cy + (int16_t)(openH * 0.85f);
    int16_t spanH = max((int16_t)1, (int16_t)(botY - topY));

    for (int16_t y = topY; y <= botY; y++) {
        float p = (float)(y - topY) / (float)spanH;
        float curve = sqrtf(max(0.0f, 1.0f - powf(p * 2.0f - 1.0f, 2.0f)));
        int16_t curW = (int16_t)(halfW * (0.65f + 0.35f * curve));
        int16_t x0 = cx - curW;
        int16_t x1 = cx + curW;

        for (int16_t x = x0; x <= x1; x++) {
            if (x < 0 || x >= LCD_WIDTH || y < 0 || y >= LCD_HEIGHT) continue;
            uint16_t pixel = COL_BMO_MOUTH_BG;

            if (p < 0.32f && abs(x - cx) < curW - 2) {
                pixel = COL_WHITE;
            } else if (p > 0.52f && abs(x - cx) < (int16_t)(curW * 0.78f)) {
                pixel = COL_BMO_TONGUE;
            }

            if (x <= x0 + 1 || x >= x1 - 1 || y <= topY + 1 || y >= botY - 1) {
                pixel = COL_BMO_INK;
            }

            frameBuffer[y * LCD_WIDTH + x] = pixel;
        }
    }
}

// 5x7 Basic ASCII Font Table
static const uint8_t FONT_DATA[][5] = {
    {0x7E, 0x11, 0x11, 0x11, 0x7E}, // A (0)
    {0x7F, 0x49, 0x49, 0x49, 0x36}, // B
    {0x3E, 0x41, 0x41, 0x41, 0x22}, // C
    {0x7F, 0x41, 0x41, 0x22, 0x1C}, // D
    {0x7F, 0x49, 0x49, 0x49, 0x41}, // E
    {0x7F, 0x09, 0x09, 0x09, 0x01}, // F
    {0x3E, 0x41, 0x49, 0x49, 0x7A}, // G
    {0x7F, 0x08, 0x08, 0x08, 0x7F}, // H
    {0x00, 0x41, 0x7F, 0x41, 0x00}, // I
    {0x20, 0x40, 0x41, 0x3F, 0x01}, // J
    {0x7F, 0x08, 0x14, 0x22, 0x41}, // K
    {0x7F, 0x40, 0x40, 0x40, 0x40}, // L
    {0x7F, 0x02, 0x0C, 0x02, 0x7F}, // M
    {0x7F, 0x04, 0x08, 0x10, 0x7F}, // N
    {0x3E, 0x41, 0x41, 0x41, 0x3E}, // O
    {0x7F, 0x09, 0x09, 0x09, 0x06}, // P
    {0x3E, 0x41, 0x51, 0x21, 0x5E}, // Q
    {0x7F, 0x09, 0x19, 0x29, 0x46}, // R
    {0x46, 0x49, 0x49, 0x49, 0x31}, // S
    {0x01, 0x01, 0x7F, 0x01, 0x01}, // T
    {0x3F, 0x40, 0x40, 0x40, 0x3F}, // U
    {0x1F, 0x20, 0x40, 0x20, 0x1F}, // V
    {0x7F, 0x20, 0x18, 0x20, 0x7F}, // W
    {0x63, 0x14, 0x08, 0x14, 0x63}, // X
    {0x07, 0x08, 0x70, 0x08, 0x07}, // Y
    {0x61, 0x51, 0x49, 0x45, 0x43}, // Z (25)
    {0x3E, 0x51, 0x49, 0x45, 0x3E}, // 0 (26)
    {0x00, 0x42, 0x7F, 0x40, 0x00}, // 1
    {0x42, 0x61, 0x51, 0x49, 0x46}, // 2
    {0x21, 0x41, 0x45, 0x4B, 0x31}, // 3
    {0x18, 0x14, 0x12, 0x7F, 0x10}, // 4
    {0x27, 0x45, 0x45, 0x45, 0x39}, // 5
    {0x3C, 0x4A, 0x49, 0x49, 0x30}, // 6
    {0x01, 0x71, 0x09, 0x05, 0x03}, // 7
    {0x36, 0x49, 0x49, 0x49, 0x36}, // 8
    {0x06, 0x49, 0x49, 0x29, 0x1E}, // 9 (35)
    {0x00, 0x36, 0x36, 0x00, 0x00}, // : (36)
    {0x08, 0x08, 0x08, 0x08, 0x08}, // - (37)
    {0x00, 0x60, 0x60, 0x00, 0x00}, // . (38)
    {0x00, 0x00, 0x00, 0x00, 0x00}  // space (39)
};

static void bufDrawChar(int16_t x, int16_t y, char c, uint16_t color, uint16_t bg, uint8_t scale = 1) {
    int idx = 39;
    if (c >= 'A' && c <= 'Z') idx = c - 'A';
    else if (c >= 'a' && c <= 'z') idx = c - 'a';
    else if (c >= '0' && c <= '9') idx = 26 + (c - '0');
    else if (c == ':') idx = 36;
    else if (c == '-') idx = 37;
    else if (c == '.') idx = 38;

    for (int col = 0; col < 5; col++) {
        uint8_t line = (idx >= 0 && idx < 40) ? FONT_DATA[idx][col] : 0;
        for (int row = 0; row < 7; row++) {
            uint16_t px = (line & (1 << row)) ? color : bg;
            bufFillRect(x + col * scale, y + row * scale, scale, scale, px);
        }
    }
}

static void bufDrawString(int16_t x, int16_t y, const char* str, uint16_t color, uint16_t bg, uint8_t scale = 1) {
    while (*str) {
        bufDrawChar(x, y, *str, color, bg, scale);
        x += (5 + 1) * scale;
        str++;
    }
}

// Push region from PSRAM framebuffer directly to ST7789 in one continuous burst
static void pushBuffer(int16_t x, int16_t y, int16_t w, int16_t h) {
    if (!spiLcd || !frameBuffer) return;
    if (x < 0) { w += x; x = 0; }
    if (y < 0) { h += y; y = 0; }
    if (x + w > LCD_WIDTH) w = LCD_WIDTH - x;
    if (y + h > LCD_HEIGHT) h = LCD_HEIGHT - y;
    if (w <= 0 || h <= 0) return;

    setWindow((uint16_t)x, (uint16_t)y, (uint16_t)(x + w - 1), (uint16_t)(y + h - 1));

    digitalWrite(LCD_PIN_DC, HIGH);
    digitalWrite(LCD_PIN_CS, LOW);
    spiLcd->beginTransaction(spiSettings);

    if (x == 0 && w == LCD_WIDTH) {
        uint8_t* ptr = (uint8_t*)&frameBuffer[y * LCD_WIDTH];
        uint32_t totalBytes = (uint32_t)w * (uint32_t)h * 2;
        spiLcd->writeBytes(ptr, totalBytes);
    } else {
        for (int16_t r = 0; r < h; r++) {
            uint8_t* ptr = (uint8_t*)&frameBuffer[(y + r) * LCD_WIDTH + x];
            spiLcd->writeBytes(ptr, w * 2);
        }
    }

    spiLcd->endTransaction();
    digitalWrite(LCD_PIN_CS, HIGH);
}

// =========================================================================
// FluxGarage RoboEyes Procedural Animation Engine (Double-Buffered)
// =========================================================================

enum RoboMood {
    MOOD_DEFAULT = 0,
    MOOD_HAPPY,
    MOOD_TIRED,
    MOOD_ANGRY
};

enum RoboPos {
    POS_CENTER = 0,
    POS_N,
    POS_NE,
    POS_E,
    POS_SE,
    POS_S,
    POS_SW,
    POS_W,
    POS_NW
};

struct RoboEyesEngine {
    uint16_t bgColor = COL_BG;
    uint16_t mainColor = COL_GLOW;

    // Geometry defaults (320x240 screen)
    int eyeLwidthDefault = 82;
    int eyeLheightDefault = 92;
    int eyeLwidthCurrent = 82;
    int eyeLheightCurrent = 92;
    int eyeLwidthNext = 82;
    int eyeLheightNext = 92;
    int eyeLheightOffset = 0;
    int eyeLborderRadiusDefault = 18;
    int eyeLborderRadiusCurrent = 18;
    int eyeLborderRadiusNext = 18;

    int eyeRwidthDefault = 82;
    int eyeRheightDefault = 92;
    int eyeRwidthCurrent = 82;
    int eyeRheightCurrent = 92;
    int eyeRwidthNext = 82;
    int eyeRheightNext = 92;
    int eyeRheightOffset = 0;
    int eyeRborderRadiusDefault = 18;
    int eyeRborderRadiusCurrent = 18;
    int eyeRborderRadiusNext = 18;

    int spaceBetweenDefault = 32;
    int spaceBetweenCurrent = 32;
    int spaceBetweenNext = 32;

    int eyeLx = 62;
    int eyeLy = 74;
    int eyeLxNext = 62;
    int eyeLyNext = 74;

    int eyeRx = 176;
    int eyeRy = 74;
    int eyeRxNext = 176;
    int eyeRyNext = 74;

    // Eyelids
    int eyelidsTiredHeight = 0;
    int eyelidsTiredHeightNext = 0;
    int eyelidsAngryHeight = 0;
    int eyelidsAngryHeightNext = 0;
    int eyelidsHappyBottomOffset = 0;
    int eyelidsHappyBottomOffsetNext = 0;

    // Active flags
    RoboMood mood = MOOD_DEFAULT;
    bool curious = false;
    bool cyclops = false;
    bool sweat = false;
    bool eyeL_open = true;
    bool eyeR_open = true;

    // Automated behaviors
    bool autoblinker = true;
    unsigned long blinkTimer = 0;

    bool idle = true;
    unsigned long idleTimer = 0;

    // Macro animations
    bool laugh = false;
    unsigned long laughTimer = 0;
    int laughDuration = 550;

    bool confused = false;
    unsigned long confusedTimer = 0;
    int confusedDuration = 550;

    // Marvin signature animations
    bool eyeRoll = false;
    unsigned long eyeRollTimer = 0;
    int eyeRollDuration = 2200;

    bool sigh = false;
    unsigned long sighTimer = 0;
    int sighDuration = 2000;

    // Flickers
    bool hFlicker = false;
    bool hFlickerAlternate = false;
    int hFlickerAmplitude = 16;

    bool vFlicker = false;
    bool vFlickerAlternate = false;
    int vFlickerAmplitude = 8;

    // Sweat drop
    float sweatY = 6.0f;
    float sweatX = 160.0f;
    float sweatW = 4.0f;
    float sweatH = 8.0f;
    float sweatYMax = 55.0f;

    // Frame-rate throttle & dirty tracking
    unsigned long lastFrameMs = 0;
    const unsigned long frameIntervalMs = 30; // 33 FPS rock-solid rendering
    bool hasChanged = true;
    char lastLabel[32] = "";

    void init() {
        eyeLxNext = (LCD_WIDTH - (eyeLwidthDefault + spaceBetweenDefault + eyeRwidthDefault)) / 2;
        eyeLyNext = (LCD_HEIGHT - eyeLheightDefault) / 2;
        eyeRxNext = eyeLxNext + eyeLwidthDefault + spaceBetweenDefault;
        eyeRyNext = eyeLyNext;

        eyeLx = eyeLxNext;
        eyeLy = eyeLyNext;
        eyeRx = eyeRxNext;
        eyeRy = eyeRyNext;

        blinkTimer = millis() + 3500;
        idleTimer = millis() + 3000;
        hasChanged = true;
    }

    void setMood(RoboMood m) {
        mood = m;
        switch (mood) {
            case MOOD_HAPPY:
                eyelidsHappyBottomOffsetNext = eyeLheightCurrent / 2;
                eyelidsTiredHeightNext = 0;
                eyelidsAngryHeightNext = 0;
                mainColor = COL_HAPPY;
                break;
            case MOOD_TIRED:
                eyelidsTiredHeightNext = eyeLheightCurrent / 2;
                eyelidsAngryHeightNext = 0;
                eyelidsHappyBottomOffsetNext = 0;
                mainColor = COL_WARN;
                break;
            case MOOD_ANGRY:
                eyelidsAngryHeightNext = eyeLheightCurrent / 2;
                eyelidsTiredHeightNext = 0;
                eyelidsHappyBottomOffsetNext = 0;
                mainColor = COL_RED;
                break;
            case MOOD_DEFAULT:
            default:
                eyelidsHappyBottomOffsetNext = 0;
                eyelidsTiredHeightNext = 0;
                eyelidsAngryHeightNext = 0;
                mainColor = COL_GLOW;
                break;
        }
        hasChanged = true;
    }

    void setPosition(RoboPos pos) {
        int maxDx = LCD_WIDTH - (eyeLwidthCurrent + spaceBetweenCurrent + eyeRwidthCurrent);
        int maxDy = LCD_HEIGHT - eyeLheightDefault;
        switch (pos) {
            case POS_N:  eyeLxNext = maxDx / 2; eyeLyNext = 18; break;
            case POS_NE: eyeLxNext = maxDx - 14; eyeLyNext = 18; break;
            case POS_E:  eyeLxNext = maxDx - 14; eyeLyNext = maxDy / 2; break;
            case POS_SE: eyeLxNext = maxDx - 14; eyeLyNext = maxDy - 14; break;
            case POS_S:  eyeLxNext = maxDx / 2; eyeLyNext = maxDy - 14; break;
            case POS_SW: eyeLxNext = 14; eyeLyNext = maxDy - 14; break;
            case POS_W:  eyeLxNext = 14; eyeLyNext = maxDy / 2; break;
            case POS_NW: eyeLxNext = 14; eyeLyNext = 18; break;
            case POS_CENTER:
            default:
                eyeLxNext = maxDx / 2;
                eyeLyNext = maxDy / 2;
                break;
        }
        hasChanged = true;
    }

    void setGaze(float gx, float gy) {
        int maxDx = LCD_WIDTH - (eyeLwidthCurrent + spaceBetweenCurrent + eyeRwidthCurrent);
        int maxDy = LCD_HEIGHT - eyeLheightDefault;
        int cx = maxDx / 2;
        int cy = maxDy / 2;
        int spanX = maxDx / 2 - 14;
        int spanY = maxDy / 2 - 14;
        eyeLxNext = cx + (int)(constrain(gx, -1.0f, 1.0f) * spanX);
        eyeLyNext = cy - (int)(constrain(gy, -1.0f, 1.0f) * spanY);
        hasChanged = true;
    }

    void blink() {
        eyeLheightNext = 2;
        eyeRheightNext = 2;
        eyeL_open = false;
        eyeR_open = false;
        hasChanged = true;
    }

    void anim_laugh() {
        laugh = true;
        vFlicker = true;
        laughTimer = millis();
        hasChanged = true;
    }

    void anim_confused() {
        confused = true;
        hFlicker = true;
        confusedTimer = millis();
        hasChanged = true;
    }

    void anim_eyeroll() {
        eyeRoll = true;
        eyeRollTimer = millis();
        hasChanged = true;
    }

    void anim_sigh() {
        sigh = true;
        sighTimer = millis();
        hasChanged = true;
    }

    bool update(unsigned long now) {
        // Frame-rate throttle: 33 FPS
        if (now - lastFrameMs < frameIntervalMs) {
            return false;
        }
        lastFrameMs = now;

        // 1. Process Macro Laugh
        if (laugh) {
            if (now >= laughTimer + laughDuration) {
                vFlicker = false;
                laugh = false;
            }
            hasChanged = true;
        }

        // 2. Process Macro Confused
        if (confused) {
            if (now >= confusedTimer + confusedDuration) {
                hFlicker = false;
                confused = false;
            }
            hasChanged = true;
        }

        // 3. Process Marvin Eye Roll
        if (eyeRoll) {
            float p = (float)(now - eyeRollTimer) / (float)eyeRollDuration;
            if (p >= 1.0f) {
                eyeRoll = false;
                setPosition(POS_CENTER);
            } else {
                float angle = p * (4.0f * 3.14159265f);
                int maxDx = LCD_WIDTH - (eyeLwidthCurrent + spaceBetweenCurrent + eyeRwidthCurrent);
                int maxDy = LCD_HEIGHT - eyeLheightDefault;
                eyeLxNext = (maxDx / 2) + (int)(cosf(angle) * 36.0f);
                eyeLyNext = (maxDy / 2) + (int)(sinf(angle) * 24.0f);
            }
            hasChanged = true;
        }

        // 4. Process Marvin Sigh
        if (sigh) {
            float p = (float)(now - sighTimer) / (float)sighDuration;
            if (p >= 1.0f) {
                sigh = false;
            } else {
                setMood(MOOD_TIRED);
                int maxDy = LCD_HEIGHT - eyeLheightDefault;
                eyeLyNext = (maxDy / 2) + (int)(p * 20.0f);
            }
            hasChanged = true;
        }

        // 5. Curiosity gaze: outer eye height expansion
        if (curious) {
            int maxDx = LCD_WIDTH - (eyeLwidthCurrent + spaceBetweenCurrent + eyeRwidthCurrent);
            if (eyeLxNext <= 20) { eyeLheightOffset = 18; }
            else if (eyeLxNext >= maxDx - 20 && cyclops) { eyeLheightOffset = 18; }
            else { eyeLheightOffset = 0; }

            if (eyeRxNext >= LCD_WIDTH - eyeRwidthCurrent - 20) { eyeRheightOffset = 18; }
            else { eyeRheightOffset = 0; }
        } else {
            eyeLheightOffset = 0;
            eyeRheightOffset = 0;
        }

        // 6. Smooth Tweening Calculations (Exponential Ease-Out with deadband)
        auto ease = [](int& cur, int next) {
            int diff = next - cur;
            if (abs(diff) <= 1) {
                cur = next;
            } else {
                cur += diff / 2;
            }
        };

        int oldLh = eyeLheightCurrent, oldRh = eyeRheightCurrent;
        int oldLx = eyeLx, oldLy = eyeLy;

        ease(eyeLheightCurrent, eyeLheightNext + eyeLheightOffset);
        ease(eyeRheightCurrent, eyeRheightNext + eyeRheightOffset);

        // Auto re-open after blink bottom
        if (!eyeL_open && eyeLheightCurrent <= 3) {
            eyeLheightNext = eyeLheightDefault;
            eyeL_open = true;
        }
        if (!eyeR_open && eyeRheightCurrent <= 3) {
            eyeRheightNext = eyeRheightDefault;
            eyeR_open = true;
        }

        ease(eyeLwidthCurrent, eyeLwidthNext);
        ease(eyeRwidthCurrent, eyeRwidthNext);
        ease(spaceBetweenCurrent, spaceBetweenNext);

        ease(eyeLx, eyeLxNext);
        ease(eyeLy, eyeLyNext);

        if (cyclops) {
            eyeRxNext = eyeLxNext;
            eyeRyNext = eyeLyNext;
            eyeRx = eyeLx;
            eyeRy = eyeLy;
        } else {
            eyeRxNext = eyeLxNext + eyeLwidthCurrent + spaceBetweenCurrent;
            eyeRyNext = eyeLyNext;
            ease(eyeRx, eyeRxNext);
            ease(eyeRy, eyeRyNext);
        }

        ease(eyeLborderRadiusCurrent, eyeLborderRadiusNext);
        ease(eyeRborderRadiusCurrent, eyeRborderRadiusNext);

        ease(eyelidsTiredHeight, eyelidsTiredHeightNext);
        ease(eyelidsAngryHeight, eyelidsAngryHeightNext);
        ease(eyelidsHappyBottomOffset, eyelidsHappyBottomOffsetNext);

        if (eyeLheightCurrent != oldLh || eyeRheightCurrent != oldRh || eyeLx != oldLx || eyeLy != oldLy) {
            hasChanged = true;
        }

        // 7. Auto-blinker timer (random between 3.5s and 6.5s)
        if (autoblinker && !eyeRoll && !laugh && !confused) {
            if (now >= blinkTimer) {
                blink();
                blinkTimer = now + random(3500, 6500);
            }
        }

        // 8. Idle wandering glance timer (random between 3.0s and 6.0s)
        if (idle && !laugh && !confused && !eyeRoll && !sigh) {
            if (now >= idleTimer) {
                int r = random(9);
                setPosition((RoboPos)r);
                idleTimer = now + random(3000, 6000);
            }
        }

        if (sweat) hasChanged = true;
        if (hFlicker || vFlicker) hasChanged = true;

        bool shouldRender = hasChanged;
        hasChanged = false;
        return shouldRender;
    }

    void render(bool forceFullWipe) {
        if (!frameBuffer) return;

        // Apply flicker offsets
        int drawLx = eyeLx;
        int drawLy = eyeLy;
        int drawRx = eyeRx;
        int drawRy = eyeRy;

        if (hFlicker) {
            drawLx += hFlickerAlternate ? hFlickerAmplitude : -hFlickerAmplitude;
            drawRx += hFlickerAlternate ? hFlickerAmplitude : -hFlickerAmplitude;
            hFlickerAlternate = !hFlickerAlternate;
        }
        if (vFlicker) {
            drawLy += vFlickerAlternate ? vFlickerAmplitude : -vFlickerAmplitude;
            drawRy += vFlickerAlternate ? vFlickerAmplitude : -vFlickerAmplitude;
            vFlickerAlternate = !vFlickerAlternate;
        }

        int curLw = eyeLwidthCurrent;
        int curLh = eyeLheightCurrent;
        int curRw = cyclops ? 0 : eyeRwidthCurrent;
        int curRh = cyclops ? 0 : eyeRheightCurrent;

        // HUD Text Label
        char label[32];
        if (cyclops) snprintf(label, sizeof(label), "ROBOEYES . CYCLOPS");
        else if (laugh) snprintf(label, sizeof(label), "ROBOEYES . LAUGH");
        else if (confused) snprintf(label, sizeof(label), "ROBOEYES . CONFUSED");
        else if (eyeRoll) snprintf(label, sizeof(label), "ROBOEYES . EYE ROLL");
        else if (mood == MOOD_HAPPY) snprintf(label, sizeof(label), "ROBOEYES . HAPPY");
        else if (mood == MOOD_TIRED) snprintf(label, sizeof(label), "ROBOEYES . TIRED");
        else if (mood == MOOD_ANGRY) snprintf(label, sizeof(label), "ROBOEYES . ANGRY");
        else snprintf(label, sizeof(label), "ROBOEYES . NEUTRAL");

        // 1. Compose Entire Frame in PSRAM Offscreen Buffer (instantaneous in RAM!)
        bufFillRect(0, 0, LCD_WIDTH, LCD_HEIGHT, bgColor);
        bufDrawString(14, 12, label, COL_GLOW_DIM, bgColor);

        // 2. Draw Left Eye
        bufFillRoundRect(drawLx, drawLy, curLw, curLh, eyeLborderRadiusCurrent, mainColor);

        // 3. Draw Right Eye (unless in cyclops mode)
        if (!cyclops) {
            bufFillRoundRect(drawRx, drawRy, curRw, curRh, eyeRborderRadiusCurrent, mainColor);
        }

        // 4. Draw Mood Eyelid Overlays
        if (eyelidsTiredHeight > 1) {
            if (!cyclops) {
                bufFillTriangle(drawLx - 1, drawLy - 1, drawLx + curLw + 1, drawLy - 1, drawLx - 1, drawLy + eyelidsTiredHeight, bgColor);
                bufFillTriangle(drawRx - 1, drawRy - 1, drawRx + curRw + 1, drawRy - 1, drawRx + curRw + 1, drawRy + eyelidsTiredHeight, bgColor);
            } else {
                bufFillTriangle(drawLx - 1, drawLy - 1, drawLx + curLw / 2, drawLy - 1, drawLx - 1, drawLy + eyelidsTiredHeight, bgColor);
                bufFillTriangle(drawLx + curLw / 2, drawLy - 1, drawLx + curLw + 1, drawLy - 1, drawLx + curLw + 1, drawLy + eyelidsTiredHeight, bgColor);
            }
        }

        if (eyelidsAngryHeight > 1) {
            if (!cyclops) {
                bufFillTriangle(drawLx - 1, drawLy - 1, drawLx + curLw + 1, drawLy - 1, drawLx + curLw + 1, drawLy + eyelidsAngryHeight, bgColor);
                bufFillTriangle(drawRx - 1, drawRy - 1, drawRx + curRw + 1, drawRy - 1, drawRx - 1, drawRy + eyelidsAngryHeight, bgColor);
            } else {
                bufFillTriangle(drawLx - 1, drawLy - 1, drawLx + curLw / 2, drawLy - 1, drawLx + curLw / 2, drawLy + eyelidsAngryHeight, bgColor);
                bufFillTriangle(drawLx + curLw / 2, drawLy - 1, drawLx + curLw + 1, drawLy - 1, drawLx + curLw / 2, drawLy + eyelidsAngryHeight, bgColor);
            }
        }

        if (eyelidsHappyBottomOffset > 1) {
            bufFillRoundRect(drawLx - 2, (drawLy + curLh) - eyelidsHappyBottomOffset, curLw + 4, eyeLheightDefault, eyeLborderRadiusCurrent, bgColor);
            if (!cyclops) {
                bufFillRoundRect(drawRx - 2, (drawRy + curRh) - eyelidsHappyBottomOffset, curRw + 4, eyeRheightDefault, eyeRborderRadiusCurrent, bgColor);
            }
        }

        // 5. Sweat Drop
        if (sweat) {
            if (sweatY < sweatYMax) {
                sweatY += 2.5f;
            } else {
                sweatY = 6.0f;
                sweatX = random(40, LCD_WIDTH - 60);
            }
            bufFillRoundRect((int16_t)sweatX, (int16_t)sweatY, (int16_t)sweatW, (int16_t)sweatH, 2, mainColor);
        }

        // 6. Push Composite Frame directly to ST7789 in ONE continuous SPI burst! Zero flicker!
        pushBuffer(0, 0, LCD_WIDTH, LCD_HEIGHT);
    }
};

static RoboEyesEngine roboEyes;

class BMOFaceEngine {
public:
    RoboMood mood = MOOD_DEFAULT;
    float curGazeX = 0.0f;
    float curGazeY = 0.0f;
    float targetGazeX = 0.0f;
    float targetGazeY = 0.0f;

    // Fluid Blinking (phase & factor)
    float blinkFactor = 1.0f; // 1.0 = wide open, 0.0 = fully shut
    int blinkPhase = 0;       // 0: idle, 1: closing, 2: opening
    unsigned long nextBlinkTime = 0;

    // Speaking & Dynamic Lip-Sync
    bool isTalking = false;
    float mouthOpen = 0.0f;

    // Animations & Expressions
    bool laugh = false;
    unsigned long laughStartTime = 0;
    bool confused = false;
    unsigned long confusedStartTime = 0;
    bool curious = false;
    bool sweat = false;

    // Autonomous Saccades
    unsigned long nextGlanceTime = 0;
    unsigned long glanceReturnTime = 0;

    bool hasChanged = true;
    unsigned long lastRenderTime = 0;
    const unsigned long frameIntervalMs = 28; // ~35 FPS

    void init() {
        nextBlinkTime = millis() + 2000;
        nextGlanceTime = millis() + 3500;
        glanceReturnTime = 0;
        hasChanged = true;
    }

    void setGaze(float gx, float gy) {
        targetGazeX = constrain(gx, -1.0f, 1.0f);
        targetGazeY = constrain(gy, -1.0f, 1.0f);
        glanceReturnTime = 0; // manual override holds gaze
        hasChanged = true;
    }

    void triggerBlink() {
        blinkPhase = 1;
        hasChanged = true;
    }

    bool update(unsigned long now) {
        if (now - lastRenderTime < frameIntervalMs) return false;

        // 1. Organic Fluid Blinking (squash & stretch)
        if (mood != MOOD_TIRED && now >= nextBlinkTime && blinkPhase == 0) {
            blinkPhase = 1;
            bool doubleBlink = (random(0, 100) < 22);
            nextBlinkTime = now + (doubleBlink ? 280 : random(3000, 5800));
            hasChanged = true;
        }

        if (blinkPhase == 1) {
            blinkFactor -= 0.32f;
            if (blinkFactor <= 0.0f) {
                blinkFactor = 0.0f;
                blinkPhase = 2;
            }
            hasChanged = true;
        } else if (blinkPhase == 2) {
            blinkFactor += 0.28f;
            if (blinkFactor >= 1.0f) {
                blinkFactor = 1.0f;
                blinkPhase = 0;
            }
            hasChanged = true;
        }

        // 2. Autonomous Living Saccades (quick natural eye darts & returns)
        if (targetGazeX == 0.0f && targetGazeY == 0.0f && now >= nextGlanceTime) {
            static const float glanceX[] = { 0.40f, -0.40f, 0.25f, -0.25f, 0.35f, -0.35f, 0.0f };
            static const float glanceY[] = { -0.30f, -0.30f, 0.20f, 0.20f, 0.0f, 0.0f, -0.25f };
            int idx = random(0, 7);
            targetGazeX = glanceX[idx];
            targetGazeY = glanceY[idx];
            glanceReturnTime = now + random(900, 1600);
            nextGlanceTime = now + random(3500, 6500);
            hasChanged = true;
        } else if (glanceReturnTime > 0 && now >= glanceReturnTime) {
            targetGazeX = 0.0f;
            targetGazeY = 0.0f;
            glanceReturnTime = 0;
            hasChanged = true;
        }

        // Snappy spring-damped eye tracking
        float dx = targetGazeX - curGazeX;
        float dy = targetGazeY - curGazeY;
        if (fabsf(dx) > 0.015f) { curGazeX += dx * 0.40f; hasChanged = true; }
        else curGazeX = targetGazeX;
        if (fabsf(dy) > 0.015f) { curGazeY += dy * 0.40f; hasChanged = true; }
        else curGazeY = targetGazeY;

        // 3. Dynamic Multi-Harmonic Speech Lip-Sync
        if (isTalking) {
            float t = (float)now * 0.001f;
            float wave1 = sinf(t * 15.0f);
            float wave2 = sinf(t * 26.0f + 0.9f);
            float wave3 = sinf(t * 6.2f);
            float targetOpen = 0.45f + 0.35f * wave1 + 0.22f * wave2 + 0.18f * wave3;
            if (targetOpen < 0.14f) targetOpen = 0.0f; // Crisp consonant closures (P/B/M)
            targetOpen = constrain(targetOpen, 0.0f, 1.0f);
            mouthOpen += (targetOpen - mouthOpen) * 0.65f;
            hasChanged = true;
        } else if (mouthOpen > 0.02f) {
            mouthOpen *= 0.55f;
            if (mouthOpen < 0.02f) mouthOpen = 0.0f;
            hasChanged = true;
        }

        // 4. Expression timers
        if (laugh) {
            if (now >= laughStartTime + 1800) laugh = false;
            hasChanged = true;
        }
        if (confused) {
            if (now >= confusedStartTime + 1800) confused = false;
            hasChanged = true;
        }

        return true;
    }

    void render(unsigned long now, bool fullWipe) {
        lastRenderTime = now;
        hasChanged = false;

        // 1. Mint CRT Screen background
        bufFillRect(0, 0, LCD_WIDTH, LCD_HEIGHT, COL_BMO_BG);

        // 2. Bezel border
        bufFillRect(0, 0, LCD_WIDTH, 3, COL_BMO_BORDER);
        bufFillRect(0, LCD_HEIGHT - 3, LCD_WIDTH, 3, COL_BMO_BORDER);
        bufFillRect(0, 0, 3, LCD_HEIGHT, COL_BMO_BORDER);
        bufFillRect(LCD_WIDTH - 3, 0, 3, LCD_HEIGHT, COL_BMO_BORDER);

        // 3. Dynamic Organic Motion (breathing bob + jaw talk + emotion hop)
        int cx = LCD_WIDTH / 2; // 160
        int cy = 118;

        // Gentle breathing float
        float breathe = sinf((float)now * 0.0032f) * 2.8f;
        int bobY = (int)breathe;
        int jitterX = 0;

        // Emotion bounces
        if (mood == MOOD_HAPPY || laugh) {
            bobY += (int)(fabsf(sinf(now * 0.016f)) * -7.0f); // Joyful bounce!
        }
        if (laugh) {
            jitterX = (now % 3) - 1; // Laugh chuckle rumble
        }
        if (confused) {
            jitterX = (int)(sinf(now * 0.055f) * 4.5f); // Nervous tremble
        }
        if (isTalking) {
            bobY += (int)(mouthOpen * 2.2f); // Jaw motion during speech
        }

        // Scaled eye positions: eyeSep = 78 (left 82, right 238)
        int gx = (int)(curGazeX * 24.0f);
        int gy = (int)(curGazeY * 16.0f);

        int eyeLx = cx - 78 + gx + jitterX;
        int eyeRx = cx + 78 + gx + jitterX;
        int eyeY = cy - 12 + gy + bobY;

        // 4. Large Rosy Cheek Blushes: cx +/- 98, y = eyeY + 22, rx = 17, ry = 9
        int blushRx = (mood == MOOD_HAPPY || laugh) ? 19 : 17;
        int blushRy = (mood == MOOD_HAPPY || laugh) ? 10 : 9;
        bufFillEllipse(cx - 98 + (int)(gx * 0.4f), eyeY + 22 + (int)(gy * 0.4f), blushRx, blushRy, COL_BMO_BLUSH);
        bufFillEllipse(cx + 98 + (int)(gx * 0.4f), eyeY + 22 + (int)(gy * 0.4f), blushRx, blushRy, COL_BMO_BLUSH);

        // 5. Large & Dynamic Eyes
        if (blinkFactor < 0.18f) {
            // Horizontal ink line when shut (thickness 4)
            bufDrawThickLine(eyeLx - 12, eyeY, eyeLx + 12, eyeY, 4, COL_BMO_INK);
            bufDrawThickLine(eyeRx - 12, eyeY, eyeRx + 12, eyeY, 4, COL_BMO_INK);
        } else if (mood == MOOD_HAPPY || laugh) {
            // Upward joyful crescent ^ ^
            bufDrawHappyArc(eyeLx, eyeY, 1.35f);
            bufDrawHappyArc(eyeRx, eyeY, 1.35f);
        } else if (mood == MOOD_TIRED) {
            // Downward sleepy curve
            bufDrawSleepyArc(eyeLx, eyeY, 1.30f);
            bufDrawSleepyArc(eyeRx, eyeY, 1.30f);
            // Floating drifting Zzz
            int zDrift = (now / 45) % 24;
            bufDrawString(240 + zDrift / 3, 50 - zDrift, "z", COL_BMO_INK, COL_BMO_BG, 2);
            bufDrawString(260 + zDrift / 2, 34 - zDrift, "Z", COL_BMO_INK, COL_BMO_BG, 3);
        } else if (mood == MOOD_ANGRY) {
            // Bold slanted eyebrows & angry eyes
            bufDrawThickLine(eyeLx - 18, eyeY - 17, eyeLx + 14, eyeY - 8, 4, COL_BMO_INK);
            bufDrawThickLine(eyeRx - 14, eyeY - 8, eyeRx + 18, eyeY - 17, 4, COL_BMO_INK);
            bufFillEllipse(eyeLx, eyeY, 9, 12, COL_BMO_INK);
            bufFillEllipse(eyeRx, eyeY, 9, 12, COL_BMO_INK);
        } else if (curious) {
            // Inquisitive look: left eye normal, right eye raised + arched eyebrow
            bufFillEllipse(eyeLx, eyeY, 10, 14, COL_BMO_INK);
            bufFillEllipse(eyeRx, eyeY - 6, 12, 16, COL_BMO_INK);
            bufDrawThickLine(eyeRx - 14, eyeY - 22, eyeRx + 14, eyeY - 17, 4, COL_BMO_INK);
        } else {
            // Large crisp BMO ink-oval eyes with squash & stretch
            int16_t erx = (int16_t)(10.5f * (1.0f + (1.0f - blinkFactor) * 0.25f));
            int16_t ery = max((int16_t)1, (int16_t)(14.5f * blinkFactor));
            bufFillEllipse(eyeLx, eyeY, erx, ery, COL_BMO_INK);
            bufFillEllipse(eyeRx, eyeY, erx, ery, COL_BMO_INK);
        }

        // 6. Dynamic Lip-Synced Mouth
        int mouthX = cx + (int)(curGazeX * 14.0f);
        int mouthY = eyeY + 26 + (int)(mouthOpen * 3.0f);
        float smileVal = (mood == MOOD_HAPPY || laugh) ? 1.0f : (mood == MOOD_TIRED ? 0.15f : (mood == MOOD_ANGRY ? -0.55f : (curious ? 0.25f : 0.70f)));
        bufDrawBmoMouthOpen(mouthX, mouthY, mouthOpen, smileVal, 1.35f);

        // 7. Extra Props: Sweat Drop
        if (sweat || confused) {
            int sx = eyeRx + 32;
            int dropOffset = ((now / 35) % 18);
            int sy = eyeY - 18 + dropOffset;
            bufFillEllipse(sx, sy, 5, 8, COL_BMO_SWEAT);
            bufFillTriangle(sx - 4, sy, sx + 4, sy, sx, sy - 10, COL_BMO_SWEAT);
        }

        pushBuffer(0, 0, LCD_WIDTH, LCD_HEIGHT);
    }
};

static BMOFaceEngine bmoFace;

// =========================================================================
// Public Face Control API
// =========================================================================

static void init() {
    Serial.println("[Face] Initializing Waveshare 2.0\" ST7789 (Zero-Flicker RoboEyes Engine)...");

    pinMode(LCD_PIN_CS, OUTPUT);
    pinMode(LCD_PIN_DC, OUTPUT);
    pinMode(LCD_PIN_RST, OUTPUT);
    pinMode(LCD_PIN_BL, OUTPUT);

    digitalWrite(LCD_PIN_CS, HIGH);
    digitalWrite(LCD_PIN_DC, HIGH);
    digitalWrite(LCD_PIN_BL, HIGH);

    if (!spiLcd) {
        spiLcd = new SPIClass(HSPI);
        spiLcd->begin(LCD_PIN_SCLK, -1, LCD_PIN_MOSI, -1);
    }

    // Hardware Reset
    digitalWrite(LCD_PIN_RST, HIGH);
    delay(20);
    digitalWrite(LCD_PIN_RST, LOW);
    delay(30);
    digitalWrite(LCD_PIN_RST, HIGH);
    delay(120);

    // ST7789 Init
    writeCmd(0x01); delay(150);  // SWRESET
    writeCmd(0x11); delay(120);  // SLPOUT
    writeCmd(0x3A); writeData(0x55);  // COLMOD 16-bit RGB565
    writeCmd(0x36); writeData(0x70);  // MADCTL Landscape 320x240
    writeCmd(0x21);              // INVON (IPS true colors)
    writeCmd(0x13);              // NORON
    writeCmd(0x29); delay(50);   // DISPON

    // Allocate 320x240 RGB565 Framebuffer in 8MB Octal PSRAM
    if (!frameBuffer) {
        frameBuffer = (uint16_t*)heap_caps_malloc(LCD_WIDTH * LCD_HEIGHT * sizeof(uint16_t), MALLOC_CAP_SPIRAM);
        if (frameBuffer) {
            Serial.printf("[Face] Allocated %u bytes framebuffer in PSRAM (Zero-Flicker Enabled).\n",
                          LCD_WIDTH * LCD_HEIGHT * sizeof(uint16_t));
        } else {
            Serial.println("[Face] WARNING: PSRAM alloc failed, allocating internal fallback.");
            frameBuffer = (uint16_t*)malloc(LCD_WIDTH * LCD_HEIGHT * sizeof(uint16_t));
        }
    }

    if (!faceMutex) {
        faceMutex = xSemaphoreCreateMutex();
    }

    roboEyes.init();
    bmoFace.init();
    needsFullWipe = true;
    needsRedraw = true;

    // Initial boot screen composed in buffer
    if (currentStyle == STYLE_BMO) {
        bufFillRect(0, 0, LCD_WIDTH, LCD_HEIGHT, COL_BMO_BG);
        bufFillRect(0, 0, LCD_WIDTH, 4, COL_BMO_BORDER);
        bufFillRect(0, LCD_HEIGHT - 4, LCD_WIDTH, 4, COL_BMO_BORDER);
        bufFillRect(0, 0, 4, LCD_HEIGHT, COL_BMO_BORDER);
        bufFillRect(LCD_WIDTH - 4, 0, 4, LCD_HEIGHT, COL_BMO_BORDER);

        // Blushes
        bufFillEllipse(160 - 98, 128, 17, 9, COL_BMO_BLUSH);
        bufFillEllipse(160 + 98, 128, 17, 9, COL_BMO_BLUSH);

        // Eyes
        bufDrawHappyArc(160 - 78, 106, 1.35f);
        bufDrawHappyArc(160 + 78, 106, 1.35f);

        // Smile
        bufDrawBmoSmile(160, 132, 0.75f, 1.35f);

        bufDrawString(105, 38, "HELLO!", COL_BMO_INK, COL_BMO_BG, 4);
        bufDrawString(65, 185, "ADVENTURE TIME BMO", COL_BMO_INK, COL_BMO_BG, 2);
    } else {
        bufFillRect(0, 0, LCD_WIDTH, LCD_HEIGHT, COL_BLUE);
        bufFillRect(4, 4, LCD_WIDTH - 8, 4, COL_YELLOW);
        bufFillRect(4, LCD_HEIGHT - 8, LCD_WIDTH - 8, 4, COL_YELLOW);
        bufFillRect(4, 4, 4, LCD_HEIGHT - 8, COL_YELLOW);
        bufFillRect(LCD_WIDTH - 8, 4, 4, LCD_HEIGHT - 8, COL_YELLOW);
        bufDrawString(85, 75, "HELLO!", COL_WHITE, COL_BLUE, 5);
        bufDrawString(48, 150, "FLUXGARAGE ROBOEYES", COL_YELLOW, COL_BLUE, 2);
    }
    pushBuffer(0, 0, LCD_WIDTH, LCD_HEIGHT);

    Serial.println("[Face] ST7789 initialized. Boot screen displayed.");
}

static void setFaceStyle(const String& styleStr) {
    bool taken = (faceMutex && xSemaphoreTake(faceMutex, pdMS_TO_TICKS(50)) == pdTRUE);
    if (!taken) return;
    msgMode = false;
    String s = styleStr;
    s.toLowerCase();
    if (s == "bmo" || s == "rocky" || s == "adventure" || s == "mint") {
        currentStyle = STYLE_BMO;
    } else if (s == "robo" || s == "roboeyes" || s == "cyan" || s == "marvin") {
        currentStyle = STYLE_ROBOEYES;
    }
    needsFullWipe = true;
    needsRedraw = true;
    xSemaphoreGive(faceMutex);
}

static void showMessage(const String& msg) {
    bool taken = (faceMutex && xSemaphoreTake(faceMutex, pdMS_TO_TICKS(50)) == pdTRUE);
    if (!taken) return;
    displayMsg = msg;
    msgMode = true;
    needsFullWipe = true;
    needsRedraw = true;
    xSemaphoreGive(faceMutex);
}

static void setRoboMood(const String& moodStr) {
    bool taken = (faceMutex && xSemaphoreTake(faceMutex, pdMS_TO_TICKS(50)) == pdTRUE);
    if (!taken) return;
    msgMode = false;
    String m = moodStr;
    m.toLowerCase();
    if (m == "bmo") {
        currentStyle = STYLE_BMO;
        needsFullWipe = true;
    } else if (m == "robo" || m == "roboeyes") {
        currentStyle = STYLE_ROBOEYES;
        needsFullWipe = true;
    } else if (m == "happy" || m == "smile" || m == "joy" || m == "excited") {
        roboEyes.setMood(MOOD_HAPPY);
        bmoFace.mood = MOOD_HAPPY;
        bmoFace.curious = false;
    } else if (m == "tired" || m == "sleepy" || m == "bored" || m == "sad") {
        roboEyes.setMood(MOOD_TIRED);
        bmoFace.mood = MOOD_TIRED;
        bmoFace.curious = false;
    } else if (m == "angry" || m == "mad" || m == "pained") {
        roboEyes.setMood(MOOD_ANGRY);
        bmoFace.mood = MOOD_ANGRY;
        bmoFace.curious = false;
    } else if (m == "curious" || m == "thinking" || m == "listening" || m == "focused") {
        roboEyes.setMood(MOOD_DEFAULT);
        bmoFace.mood = MOOD_DEFAULT;
        bmoFace.curious = true;
    } else if (m == "alarmed" || m == "shocked" || m == "surprised") {
        roboEyes.setMood(MOOD_ANGRY);
        bmoFace.mood = MOOD_ANGRY;
        bmoFace.confused = true;
        bmoFace.confusedStartTime = millis();
        bmoFace.curious = false;
    } else {
        roboEyes.setMood(MOOD_DEFAULT);
        bmoFace.mood = MOOD_DEFAULT;
        bmoFace.curious = false;
    }
    needsRedraw = true;
    xSemaphoreGive(faceMutex);
}

static void triggerRoboAnim(const String& animStr) {
    bool taken = (faceMutex && xSemaphoreTake(faceMutex, pdMS_TO_TICKS(50)) == pdTRUE);
    if (!taken) return;
    msgMode = false;
    String a = animStr;
    a.toLowerCase();
    if (a == "laugh") { roboEyes.anim_laugh(); bmoFace.laugh = true; bmoFace.laughStartTime = millis(); }
    else if (a == "confused") { roboEyes.anim_confused(); bmoFace.confused = true; bmoFace.confusedStartTime = millis(); }
    else if (a == "blink") { roboEyes.blink(); bmoFace.triggerBlink(); }
    else if (a == "sweat") { roboEyes.sweat = !roboEyes.sweat; bmoFace.sweat = !bmoFace.sweat; }
    else if (a == "curious") { roboEyes.curious = !roboEyes.curious; bmoFace.curious = !bmoFace.curious; }
    else if (a == "cyclops") roboEyes.cyclops = !roboEyes.cyclops;
    else if (a == "eye_roll" || a == "eyeroll" || a == "roll") roboEyes.anim_eyeroll();
    else if (a == "sigh") roboEyes.anim_sigh();
    needsRedraw = true;
    xSemaphoreGive(faceMutex);
}

static void setRoboPosition(const String& posStr) {
    bool taken = (faceMutex && xSemaphoreTake(faceMutex, pdMS_TO_TICKS(50)) == pdTRUE);
    if (!taken) return;
    msgMode = false;
    String p = posStr;
    p.toUpperCase();
    if (p == "N") { roboEyes.setPosition(POS_N); bmoFace.setGaze(0.0f, 0.7f); }
    else if (p == "NE") { roboEyes.setPosition(POS_NE); bmoFace.setGaze(0.7f, 0.7f); }
    else if (p == "E") { roboEyes.setPosition(POS_E); bmoFace.setGaze(0.7f, 0.0f); }
    else if (p == "SE") { roboEyes.setPosition(POS_SE); bmoFace.setGaze(0.7f, -0.7f); }
    else if (p == "S") { roboEyes.setPosition(POS_S); bmoFace.setGaze(0.0f, -0.7f); }
    else if (p == "SW") { roboEyes.setPosition(POS_SW); bmoFace.setGaze(-0.7f, -0.7f); }
    else if (p == "W") { roboEyes.setPosition(POS_W); bmoFace.setGaze(-0.7f, 0.0f); }
    else if (p == "NW") { roboEyes.setPosition(POS_NW); bmoFace.setGaze(-0.7f, 0.7f); }
    else { roboEyes.setPosition(POS_CENTER); bmoFace.setGaze(0.0f, 0.0f); }
    needsRedraw = true;
    xSemaphoreGive(faceMutex);
}

static void setRoboColor(uint16_t col) {
    bool taken = (faceMutex && xSemaphoreTake(faceMutex, pdMS_TO_TICKS(50)) == pdTRUE);
    if (!taken) return;
    roboEyes.mainColor = col;
    roboEyes.hasChanged = true;
    needsRedraw = true;
    xSemaphoreGive(faceMutex);
}

// Backward-compatible wrappers for main.cpp
static inline void setMood(const char* mood) { setRoboMood(String(mood)); }
static inline void triggerSequence(const String& seq) { triggerRoboAnim(seq); }
static inline void setState(const String& mood, float gx, float gy, bool speaking) {
    gazeX = gx;
    gazeY = gy;
    isSpeaking = speaking;
    if (!mood.isEmpty()) setRoboMood(mood);
    bool taken = (faceMutex && xSemaphoreTake(faceMutex, pdMS_TO_TICKS(50)) == pdTRUE);
    if (!taken) return;
    msgMode = false;
    roboEyes.setGaze(gx, gy);
    bmoFace.setGaze(gx, gy);
    bmoFace.isTalking = speaking;
    needsRedraw = true;
    xSemaphoreGive(faceMutex);
}

static void tick() {
    unsigned long now = millis();

    // Auto-dismiss boot banner after 3.2 seconds
    static unsigned long bootStartMs = 0;
    if (bootStartMs == 0) bootStartMs = now;
    if (msgMode && displayMsg == "HELLO" && (now - bootStartMs > 3200)) {
        msgMode = false;
        needsFullWipe = true;
        needsRedraw = true;
    }

    bool taken = (faceMutex && xSemaphoreTake(faceMutex, pdMS_TO_TICKS(20)) == pdTRUE);
    if (!taken) return;

    if (msgMode) {
        if (needsFullWipe) {
            if (currentStyle == STYLE_BMO) {
                bufFillRect(0, 0, LCD_WIDTH, LCD_HEIGHT, COL_BMO_BG);
                bufFillRect(0, 0, LCD_WIDTH, 4, COL_BMO_BORDER);
                bufFillRect(0, LCD_HEIGHT - 4, LCD_WIDTH, 4, COL_BMO_BORDER);
                bufFillRect(0, 0, 4, LCD_HEIGHT, COL_BMO_BORDER);
                bufFillRect(LCD_WIDTH - 4, 0, 4, LCD_HEIGHT, COL_BMO_BORDER);

                bufFillEllipse(160 - 98, 128, 17, 9, COL_BMO_BLUSH);
                bufFillEllipse(160 + 98, 128, 17, 9, COL_BMO_BLUSH);

                bufDrawHappyArc(160 - 78, 106, 1.35f);
                bufDrawHappyArc(160 + 78, 106, 1.35f);

                bufDrawBmoSmile(160, 132, 0.75f, 1.35f);

                bufDrawString(105, 38, "HELLO!", COL_BMO_INK, COL_BMO_BG, 4);
                bufDrawString(65, 185, "ADVENTURE TIME BMO", COL_BMO_INK, COL_BMO_BG, 2);
            } else {
                bufFillRect(0, 0, LCD_WIDTH, LCD_HEIGHT, COL_BLUE);
                bufFillRect(4, 4, LCD_WIDTH - 8, 4, COL_YELLOW);
                bufFillRect(4, LCD_HEIGHT - 8, LCD_WIDTH - 8, 4, COL_YELLOW);
                bufFillRect(4, 4, 4, LCD_HEIGHT - 8, COL_YELLOW);
                bufFillRect(LCD_WIDTH - 8, 4, 4, LCD_HEIGHT - 8, COL_YELLOW);

                int len = displayMsg.length();
                int scale = (len <= 6) ? 5 : 3;
                int totalW = len * (6 * scale) - scale;
                int startX = max(10, (LCD_WIDTH - totalW) / 2);
                bufDrawString(startX, 80, displayMsg.c_str(), COL_WHITE, COL_BLUE, scale);
                bufDrawString(60, 160, "FLUXGARAGE ROBOEYES", COL_YELLOW, COL_BLUE, 2);
            }
            pushBuffer(0, 0, LCD_WIDTH, LCD_HEIGHT);
            needsFullWipe = false;
        }
        xSemaphoreGive(faceMutex);
        return;
    }

    bool shouldRender = false;
    if (currentStyle == STYLE_BMO) {
        shouldRender = bmoFace.update(now) || needsFullWipe;
    } else {
        shouldRender = roboEyes.update(now) || needsFullWipe;
    }
    bool fullWipe = needsFullWipe;
    needsFullWipe = false;

    if (shouldRender) {
        if (currentStyle == STYLE_BMO) {
            bmoFace.render(now, fullWipe);
        } else {
            roboEyes.render(fullWipe);
        }
    }

    xSemaphoreGive(faceMutex);
}

}  // namespace marvin_face
