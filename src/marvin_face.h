#pragma once

#include <Arduino.h>
#include <SPI.h>
#include "marvin_pins.h"

namespace marvin_face {

// RGB565 Colors (Marvin GLOOM-MO palette matching voice/characters/marvin.json)
static constexpr uint16_t COL_BG       = 0x0862;  // Deep charcoal visor (#080c12)
static constexpr uint16_t COL_GLOW     = 0xB6FA;  // Mint-cyan GLOOM-MO glow (#b7dcd4)
static constexpr uint16_t COL_GLOW_DIM = 0x4A69;  // Dim eyelid / subtle frame
static constexpr uint16_t COL_HAPPY    = 0x36B3;  // Mint-emerald accent (#34d399)
static constexpr uint16_t COL_WARN     = 0xFBE4;  // Amber curious/surprised

static SPIClass* spiLcd = nullptr;
static String currentMood = "bored";
static float gazeX = 0.0f;  // -1.0 (left) .. +1.0 (right)
static float gazeY = 0.0f;  // -1.0 (down) .. +1.0 (up)
static bool isSpeaking = false;
static bool needsRedraw = true;
static unsigned long lastBlinkMs = 0;
static unsigned long lastAnimMs = 0;
static bool blinkClosed = false;

static inline void writeCmd(uint8_t c) {
    digitalWrite(LCD_PIN_DC, LOW);
    digitalWrite(LCD_PIN_CS, LOW);
    spiLcd->transfer(c);
    digitalWrite(LCD_PIN_CS, HIGH);
}

static inline void writeData(uint8_t d) {
    digitalWrite(LCD_PIN_DC, HIGH);
    digitalWrite(LCD_PIN_CS, LOW);
    spiLcd->transfer(d);
    digitalWrite(LCD_PIN_CS, HIGH);
}

static void setWindow(uint16_t x0, uint16_t y0, uint16_t x1, uint16_t y1) {
    writeCmd(0x2A);  // CASET
    writeData(x0 >> 8); writeData(x0 & 0xFF);
    writeData(x1 >> 8); writeData(x1 & 0xFF);
    writeCmd(0x2B);  // RASET
    writeData(y0 >> 8); writeData(y0 & 0xFF);
    writeData(y1 >> 8); writeData(y1 & 0xFF);
    writeCmd(0x2C);  // RAMWR
}

static void fillRect(int16_t x, int16_t y, int16_t w, int16_t h, uint16_t color) {
    if (!spiLcd || w <= 0 || h <= 0) return;
    if (x < 0) { w += x; x = 0; }
    if (y < 0) { h += y; y = 0; }
    if (x + w > LCD_WIDTH) w = LCD_WIDTH - x;
    if (y + h > LCD_HEIGHT) h = LCD_HEIGHT - y;
    if (w <= 0 || h <= 0) return;

    setWindow((uint16_t)x, (uint16_t)y, (uint16_t)(x + w - 1), (uint16_t)(y + h - 1));
    uint32_t total = (uint32_t)w * (uint32_t)h;
    uint8_t hi = color >> 8;
    uint8_t lo = color & 0xFF;

    static uint8_t buf[512];
    uint32_t chunkBytes = min((uint32_t)sizeof(buf), total * 2);
    for (uint32_t i = 0; i < chunkBytes; i += 2) {
        buf[i] = hi;
        buf[i + 1] = lo;
    }

    digitalWrite(LCD_PIN_DC, HIGH);
    digitalWrite(LCD_PIN_CS, LOW);
    spiLcd->beginTransaction(SPISettings(40000000, MSBFIRST, SPI_MODE0));
    uint32_t remaining = total * 2;
    while (remaining > 0) {
        uint32_t n = min(remaining, chunkBytes);
        spiLcd->writeBytes(buf, n);
        remaining -= n;
    }
    spiLcd->endTransaction();
    digitalWrite(LCD_PIN_CS, HIGH);
}

static void fillRoundedEye(int16_t cx, int16_t cy, int16_t rx, int16_t ry, uint16_t color) {
    for (int16_t dy = -ry; dy <= ry; dy++) {
        float norm = (float)dy / (float)max((int16_t)1, ry);
        float span = sqrtf(max(0.0f, 1.0f - norm * norm));
        int16_t hw = (int16_t)(rx * span);
        if (hw > 0) {
            fillRect(cx - hw, cy + dy, hw * 2, 1, color);
        }
    }
}

static void drawMarvinFace() {
    if (!spiLcd) return;
    fillRect(0, 0, LCD_WIDTH, LCD_HEIGHT, COL_BG);

    // Subtle visor border ticks
    fillRect(14, 14, 24, 3, COL_GLOW_DIM);
    fillRect(LCD_WIDTH - 38, 14, 24, 3, COL_GLOW_DIM);

    int16_t gx = (int16_t)(gazeX * 18.0f);
    int16_t gy = (int16_t)(-gazeY * 14.0f);
    int16_t leftCx = 98 + gx;
    int16_t rightCx = 222 + gx;
    int16_t eyeCy = 104 + gy;

    uint16_t eyeCol = COL_GLOW;
    if (currentMood == "happy") eyeCol = COL_HAPPY;
    else if (currentMood == "surprised") eyeCol = COL_WARN;

    if (blinkClosed) {
        // Horizontal closed eyelids
        fillRect(leftCx - 34, eyeCy - 2, 68, 5, eyeCol);
        fillRect(rightCx - 34, eyeCy - 2, 68, 5, eyeCol);
    } else if (currentMood == "surprised") {
        fillRoundedEye(leftCx, eyeCy, 34, 34, eyeCol);
        fillRoundedEye(rightCx, eyeCy, 34, 34, eyeCol);
        fillRoundedEye(leftCx, eyeCy, 12, 12, COL_BG);
        fillRoundedEye(rightCx, eyeCy, 12, 12, COL_BG);
    } else if (currentMood == "happy") {
        // Gentle inverted-crescent happy squint
        fillRoundedEye(leftCx, eyeCy, 34, 24, eyeCol);
        fillRect(leftCx - 38, eyeCy + 4, 76, 26, COL_BG);
        fillRoundedEye(rightCx, eyeCy, 34, 24, eyeCol);
        fillRect(rightCx - 38, eyeCy + 4, 76, 26, COL_BG);
    } else {
        // Classic Marvin heavy-lidded bored / sad / paranoid android eyes
        fillRoundedEye(leftCx, eyeCy, 36, 26, eyeCol);
        fillRoundedEye(rightCx, eyeCy, 36, 26, eyeCol);

        // Slanted downcast upper eyelids (sadder angle if mood == "sad")
        int16_t droop = (currentMood == "sad") ? 14 : 9;
        for (int16_t x = -38; x <= 38; x++) {
            int16_t lidLeftY = (eyeCy - 18) + (int16_t)((float)(-x + 38) / 76.0f * droop);
            int16_t lidRightY = (eyeCy - 18) + (int16_t)((float)(x + 38) / 76.0f * droop);
            fillRect(leftCx + x, eyeCy - 32, 1, lidLeftY - (eyeCy - 32), COL_BG);
            fillRect(rightCx + x, eyeCy - 32, 1, lidRightY - (eyeCy - 32), COL_BG);
        }

        // Tiny tired pupils
        fillRoundedEye(leftCx + (int16_t)(gazeX * 5.0f), eyeCy + 4, 7, 7, COL_BG);
        fillRoundedEye(rightCx + (int16_t)(gazeX * 5.0f), eyeCy + 4, 7, 7, COL_BG);
    }

    // Mouth / Deadpan Voice Indicator Bar
    int16_t mouthY = 186;
    if (isSpeaking) {
        int16_t amp = 6 + (int16_t)((millis() / 90) % 3) * 6;
        for (int i = -3; i <= 3; i++) {
            int16_t barH = max(4, amp - abs(i) * 3);
            fillRect(160 + i * 16 - 4, mouthY - barH / 2, 8, barH, eyeCol);
        }
    } else if (currentMood == "sad" || currentMood == "bored") {
        // Flat / gently downturned Marvin mouth line
        fillRect(118, mouthY, 84, 4, COL_GLOW_DIM);
        fillRect(114, mouthY + 3, 6, 4, COL_GLOW_DIM);
        fillRect(200, mouthY + 3, 6, 4, COL_GLOW_DIM);
    } else if (currentMood == "happy") {
        fillRect(126, mouthY + 2, 68, 4, eyeCol);
        fillRect(120, mouthY - 2, 8, 5, eyeCol);
        fillRect(192, mouthY - 2, 8, 5, eyeCol);
    } else {
        fillRect(142, mouthY, 36, 4, eyeCol);
    }

    needsRedraw = false;
}

static void init() {
    pinMode(LCD_PIN_CS, OUTPUT);
    pinMode(LCD_PIN_DC, OUTPUT);
    pinMode(LCD_PIN_RST, OUTPUT);
    digitalWrite(LCD_PIN_CS, HIGH);

    spiLcd = new SPIClass(HSPI);
    spiLcd->begin(LCD_PIN_SCLK, -1, LCD_PIN_MOSI, LCD_PIN_CS);

    // Hardware reset
    digitalWrite(LCD_PIN_RST, HIGH);
    delay(20);
    digitalWrite(LCD_PIN_RST, LOW);
    delay(30);
    digitalWrite(LCD_PIN_RST, HIGH);
    delay(120);

    // ST7789 init sequence (320x240 landscape, MADCTL = 0x70)
    writeCmd(0x01); delay(150);  // SWRESET
    writeCmd(0x11); delay(120);  // SLPOUT
    writeCmd(0x3A); writeData(0x55);  // 16-bit RGB565
    writeCmd(0x36); writeData(0x70);  // Landscape 320x240 (MX=0, MY=1, MV=1, ML=1)
    writeCmd(0x21);              // INVON (IPS panel true colors)
    writeCmd(0x13);              // NORON
    writeCmd(0x29); delay(20);   // DISPON

    lastBlinkMs = millis();
    needsRedraw = true;
    drawMarvinFace();
    Serial.println("[Face] ST7789 320x240 GLOOM-MO display initialized.");
}

static void setState(const String& mood, float gx, float gy, bool speaking) {
    if (mood.length() > 0) currentMood = mood;
    gazeX = constrain(gx, -1.0f, 1.0f);
    gazeY = constrain(gy, -1.0f, 1.0f);
    isSpeaking = speaking;
    needsRedraw = true;
}

static void tick() {
    unsigned long now = millis();
    // Natural blink every 4.8 seconds
    if (!blinkClosed && now - lastBlinkMs > 4800) {
        blinkClosed = true;
        lastBlinkMs = now;
        needsRedraw = true;
    } else if (blinkClosed && now - lastBlinkMs > 140) {
        blinkClosed = false;
        lastBlinkMs = now;
        needsRedraw = true;
    }

    if (isSpeaking && now - lastAnimMs > 120) {
        lastAnimMs = now;
        needsRedraw = true;
    }

    if (needsRedraw) {
        drawMarvinFace();
    }
}

}  // namespace marvin_face
