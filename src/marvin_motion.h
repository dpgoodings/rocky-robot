#pragma once

#include <Arduino.h>
#include <Wire.h>
#include <math.h>
#include "marvin_pins.h"

namespace marvin_motion {

// Standard 9g servo pulse bounds in PCA9685 12-bit ticks @ 50 Hz (4096 ticks / 20000 us)
// 500 us = 102 ticks, 1500 us = 307 ticks, 2500 us = 512 ticks
static constexpr float PWM_FREQ_HZ = 50.0f;
static constexpr float TICK_MIN = 108.0f;  // ~527 us (0 deg)
static constexpr float TICK_MAX = 504.0f;  // ~2460 us (180 deg)

static bool pcaPresent = false;
static float currentDeg[MARVIN_SERVO_COUNT];
static float targetDeg[MARVIN_SERVO_COUNT];
static float slewDegPerSec[MARVIN_SERVO_COUNT];
static int8_t trimDeg[MARVIN_SERVO_COUNT] = {0};

// Joint safe mechanical limits (degrees)
static constexpr float MIN_LIMIT[MARVIN_SERVO_COUNT] = {15, 58, 30, 50, 30, 50, 55, 65, 55, 65};
static constexpr float MAX_LIMIT[MARVIN_SERVO_COUNT] = {165, 132, 150, 140, 150, 140, 125, 115, 125, 115};

enum class GaitMode : uint8_t {
    IDLE = 0,
    WADDLE_FWD,
    WADDLE_REV,
    TURN_LEFT,
    TURN_RIGHT,
    GESTURE_SEQ
};

static volatile GaitMode activeGait = GaitMode::IDLE;
static unsigned long gaitStartMs = 0;
static unsigned long gaitDurationMs = 0;
static uint8_t activeGestureId = 0;

static void pcaWriteReg(uint8_t reg, uint8_t val) {
    Wire.beginTransmission(PCA9685_I2C_ADDR);
    Wire.write(reg);
    Wire.write(val);
    Wire.endTransmission();
}

static uint8_t pcaReadReg(uint8_t reg) {
    Wire.beginTransmission(PCA9685_I2C_ADDR);
    Wire.write(reg);
    Wire.endTransmission();
    Wire.requestFrom((uint8_t)PCA9685_I2C_ADDR, (uint8_t)1);
    return Wire.available() ? Wire.read() : 0;
}

static void pcaSetPWM(uint8_t ch, uint16_t on, uint16_t off) {
    if (!pcaPresent || ch > 15) return;
    Wire.beginTransmission(PCA9685_I2C_ADDR);
    Wire.write(0x06 + 4 * ch);
    Wire.write(on & 0xFF);
    Wire.write(on >> 8);
    Wire.write(off & 0xFF);
    Wire.write(off >> 8);
    Wire.endTransmission();
}

static float clampAngle(uint8_t ch, float deg) {
    if (ch >= MARVIN_SERVO_COUNT) return 90.0f;
    if (deg < MIN_LIMIT[ch]) return MIN_LIMIT[ch];
    if (deg > MAX_LIMIT[ch]) return MAX_LIMIT[ch];
    return deg;
}

static void writeServoDeg(uint8_t ch, float deg) {
    float a = clampAngle(ch, deg + trimDeg[ch]);
    uint16_t ticks = (uint16_t)(TICK_MIN + (a / 180.0f) * (TICK_MAX - TICK_MIN));
    pcaSetPWM(ch, 0, ticks);
}

static void setTarget(uint8_t ch, float deg, float speedDegSec = 180.0f) {
    if (ch >= MARVIN_SERVO_COUNT) return;
    targetDeg[ch] = clampAngle(ch, deg);
    slewDegPerSec[ch] = speedDegSec;
}

static void setHomePose(float speedDegSec = 120.0f) {
    activeGait = GaitMode::IDLE;
    for (uint8_t i = 0; i < MARVIN_SERVO_COUNT; i++) {
        setTarget(i, 90.0f, speedDegSec);
    }
    // Signature slight Marvin slouch on shoulders & pensive 4-deg chin dip
    setTarget(CH_HEAD_TILT, 86.0f, speedDegSec);
}

static bool init() {
    Wire.begin(MARVIN_I2C_SDA, MARVIN_I2C_SCL, 400000);
    Wire.beginTransmission(PCA9685_I2C_ADDR);
    if (Wire.endTransmission() != 0) {
        Serial.println("[Motion] WARNING: PCA9685 not detected on I2C (0x40). Running in virtual servo mode.");
        pcaPresent = false;
    } else {
        // Put PCA9685 to sleep, set prescaler for 50 Hz, enable auto-increment
        uint8_t oldmode = pcaReadReg(0x00);
        pcaWriteReg(0x00, (oldmode & 0x7F) | 0x10);
        uint8_t prescale = (uint8_t)(roundf(25000000.0f / (4096.0f * PWM_FREQ_HZ)) - 1.0f);
        pcaWriteReg(0xFE, prescale);
        pcaWriteReg(0x00, oldmode & ~0x10);
        delay(5);
        pcaWriteReg(0x00, oldmode | 0xA0);  // Restart + Auto-Increment
        pcaPresent = true;
        Serial.println("[Motion] PCA9685 16-ch PWM driver initialized (50 Hz).");
    }

    for (uint8_t i = 0; i < MARVIN_SERVO_COUNT; i++) {
        currentDeg[i] = 90.0f;
        targetDeg[i] = 90.0f;
        slewDegPerSec[i] = 140.0f;
        writeServoDeg(i, 90.0f);
        delay(25);  // Stagger initial servo power-on to avoid surge
    }
    setHomePose(90.0f);
    return pcaPresent;
}

static void setHeadLook(float panDeg, float tiltDeg, float speedDegSec = 220.0f) {
    setTarget(CH_HEAD_PAN, panDeg, speedDegSec);
    setTarget(CH_HEAD_TILT, tiltDeg, speedDegSec);
}

static void triggerAction(const String& actionRaw, unsigned long durationMs = 2200) {
    String act = actionRaw;
    act.toLowerCase();
    act.trim();

    gaitStartMs = millis();
    gaitDurationMs = durationMs;

    if (act == "step_forward" || act == "walk" || act == "waddle") {
        activeGait = GaitMode::WADDLE_FWD;
        gaitDurationMs = max(durationMs, 2600UL);
    } else if (act == "step_backward" || act == "reverse") {
        activeGait = GaitMode::WADDLE_REV;
        gaitDurationMs = max(durationMs, 2600UL);
    } else if (act == "turn_left" || act == "pivot_left") {
        activeGait = GaitMode::TURN_LEFT;
        gaitDurationMs = max(durationMs, 2000UL);
    } else if (act == "turn_right" || act == "pivot_right") {
        activeGait = GaitMode::TURN_RIGHT;
        gaitDurationMs = max(durationMs, 2000UL);
    } else if (act == "nod") {
        activeGait = GaitMode::GESTURE_SEQ;
        activeGestureId = 1;
        gaitDurationMs = 1300;
    } else if (act == "shake") {
        activeGait = GaitMode::GESTURE_SEQ;
        activeGestureId = 2;
        gaitDurationMs = 1500;
    } else if (act == "sigh" || act == "crouch" || act == "brood") {
        activeGait = GaitMode::GESTURE_SEQ;
        activeGestureId = 3;
        gaitDurationMs = 2200;
    } else if (act == "fist_bump" || act == "wave") {
        activeGait = GaitMode::GESTURE_SEQ;
        activeGestureId = 4;
        gaitDurationMs = 2200;
    } else if (act == "bow") {
        activeGait = GaitMode::GESTURE_SEQ;
        activeGestureId = 5;
        gaitDurationMs = 2000;
    } else if (act == "stand_tall" || act == "home" || act == "center") {
        setHomePose(150.0f);
    }
}

// Call periodically (~50 Hz / every 20 ms)
static void tick(uint32_t dtMs) {
    unsigned long now = millis();
    unsigned long elapsed = now - gaitStartMs;

    if (activeGait != GaitMode::IDLE) {
        if (elapsed >= gaitDurationMs) {
            setHomePose(140.0f);
        } else {
            float phase = (float)elapsed / 1000.0f;  // seconds
            float w = 2.0f * PI * 1.15f * phase;     // ~1.15 Hz gentle waddle

            if (activeGait == GaitMode::WADDLE_FWD || activeGait == GaitMode::WADDLE_REV) {
                float dir = (activeGait == GaitMode::WADDLE_FWD) ? 1.0f : -1.0f;
                float roll = sinf(w) * 14.0f;        // Ankle side-to-side weight shift
                float step = cosf(w) * 14.0f * dir;  // Hip pitch forward/back stride
                setTarget(CH_L_ANKLE_ROLL, 90.0f + roll, 240.0f);
                setTarget(CH_R_ANKLE_ROLL, 90.0f + roll, 240.0f);
                setTarget(CH_L_HIP_PITCH, 90.0f + step, 240.0f);
                setTarget(CH_R_HIP_PITCH, 90.0f - step, 240.0f);
                // Counter-swing arms & keep head level
                setTarget(CH_L_SHOULDER_PITCH, 90.0f - step * 1.2f, 220.0f);
                setTarget(CH_R_SHOULDER_PITCH, 90.0f + step * 1.2f, 220.0f);
                setTarget(CH_L_SHOULDER_ROLL, 90.0f + max(0.0f, -roll) * 0.8f, 200.0f);
                setTarget(CH_R_SHOULDER_ROLL, 90.0f - max(0.0f, roll) * 0.8f, 200.0f);
            } else if (activeGait == GaitMode::TURN_LEFT || activeGait == GaitMode::TURN_RIGHT) {
                float turnDir = (activeGait == GaitMode::TURN_LEFT) ? 1.0f : -1.0f;
                float roll = sinf(w) * 15.0f;
                float step = cosf(w) * 12.0f * turnDir;
                setTarget(CH_L_ANKLE_ROLL, 90.0f + roll, 240.0f);
                setTarget(CH_R_ANKLE_ROLL, 90.0f + roll, 240.0f);
                setTarget(CH_L_HIP_PITCH, 90.0f + step, 240.0f);
                setTarget(CH_R_HIP_PITCH, 90.0f + step, 240.0f);
            } else if (activeGait == GaitMode::GESTURE_SEQ) {
                if (activeGestureId == 1) {  // nod
                    float d = sinf((float)elapsed / 1300.0f * 2.0f * PI * 2.0f) * 16.0f;
                    setTarget(CH_HEAD_TILT, 86.0f + d, 260.0f);
                } else if (activeGestureId == 2) {  // shake
                    float d = sinf((float)elapsed / 1500.0f * 2.0f * PI * 2.0f) * 26.0f;
                    setTarget(CH_HEAD_PAN, 90.0f + d, 280.0f);
                } else if (activeGestureId == 3) {  // sigh / melancholy droop
                    setTarget(CH_HEAD_TILT, 64.0f, 90.0f);
                    setTarget(CH_L_SHOULDER_PITCH, 74.0f, 80.0f);
                    setTarget(CH_R_SHOULDER_PITCH, 74.0f, 80.0f);
                } else if (activeGestureId == 4) {  // wave / fist_bump
                    float wave = sinf(phase * 2.0f * PI * 1.8f) * 18.0f;
                    setTarget(CH_R_SHOULDER_PITCH, 140.0f, 240.0f);
                    setTarget(CH_R_SHOULDER_ROLL, 115.0f + wave, 260.0f);
                } else if (activeGestureId == 5) {  // bow
                    setTarget(CH_HEAD_TILT, 66.0f, 120.0f);
                    setTarget(CH_L_HIP_PITCH, 108.0f, 120.0f);
                    setTarget(CH_R_HIP_PITCH, 108.0f, 120.0f);
                    setTarget(CH_L_SHOULDER_PITCH, 75.0f, 120.0f);
                    setTarget(CH_R_SHOULDER_PITCH, 75.0f, 120.0f);
                }
            }
        }
    }

    float dt = (float)dtMs / 1000.0f;
    for (uint8_t i = 0; i < MARVIN_SERVO_COUNT; i++) {
        float diff = targetDeg[i] - currentDeg[i];
        float maxStep = slewDegPerSec[i] * dt;
        if (fabsf(diff) <= maxStep) {
            currentDeg[i] = targetDeg[i];
        } else {
            currentDeg[i] += (diff > 0.0f ? maxStep : -maxStep);
        }
        writeServoDeg(i, currentDeg[i]);
    }
}

}  // namespace marvin_motion
