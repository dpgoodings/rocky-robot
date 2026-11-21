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

// Servo auto-relaxation parameters (silences MG90S buzz, heat, and current draw at rest)
static constexpr float RELAX_TOLERANCE_DEG = 0.5f;
static constexpr unsigned long RELAX_AFTER_MS = 1500UL;
static constexpr float TILT_RELAX_MIN_DEG = 75.0f;
static constexpr float TILT_RELAX_MAX_DEG = 100.0f;

static bool pcaPresent = false;
static SemaphoreHandle_t motionMutex = nullptr;
static float currentDeg[MARVIN_SERVO_COUNT];
static float targetDeg[MARVIN_SERVO_COUNT];
static float slewDegPerSec[MARVIN_SERVO_COUNT];
static int8_t trimDeg[MARVIN_SERVO_COUNT] = {0};
static unsigned long lastMovedMs[MARVIN_SERVO_COUNT] = {0};
static bool channelRelaxed[MARVIN_SERVO_COUNT] = {false};
static volatile bool assemblyMode = false;

// Joint safe mechanical limits (degrees)
static constexpr float MIN_LIMIT[MARVIN_SERVO_COUNT] = {15, 58, 30, 50, 30, 50, 55, 65, 55, 65};
static constexpr float MAX_LIMIT[MARVIN_SERVO_COUNT] = {165, 132, 150, 140, 150, 140, 125, 115, 125, 115};

enum class GaitMode : uint8_t {
    IDLE = 0,
    WADDLE_FWD,
    WADDLE_REV,
    TURN_LEFT,
    TURN_RIGHT,
    GESTURE_SEQ,
    DANCE_SEQ
};

enum GestureId : uint8_t {
    GESTURE_NONE = 0,
    GESTURE_NOD = 1,
    GESTURE_SHAKE = 2,
    GESTURE_SIGH = 3,
    GESTURE_WAVE = 4,
    GESTURE_BOW = 5,
    GESTURE_EYEROLL = 6,
    GESTURE_CURIOUS_TILT = 7,
    GESTURE_HAPPY_BOUNCE = 8,
    GESTURE_SAD_DROOP = 9,
    GESTURE_SURPRISED_POP = 10
};

struct DanceStep {
    int8_t panOff;
    int8_t tiltOff;
    int8_t lShPitchOff;
    int8_t rShPitchOff;
    int8_t lShRollOff;
    int8_t rShRollOff;
    uint16_t durationMs;
    uint16_t slewDegSec;
};

// 1. Groove (~5.0 s): head bobs on the beat while swaying smoothly side-to-side
static constexpr DanceStep DANCE_GROOVE[] = {
    {  0, -10, -10,  10,   6,  -6, 290, 320},
    { 12,   6,  10, -10,  -6,   6, 290, 320},
    { 22, -10, -12,  12,   8,  -8, 290, 320},
    { 18,   6,  12, -12,  -8,   8, 290, 320},
    {  0, -10, -10,  10,   6,  -6, 290, 320},
    {-12,   6,  10, -10,  -6,   6, 290, 320},
    {-22, -10, -12,  12,   8,  -8, 290, 320},
    {-18,   6,  12, -12,  -8,   8, 290, 320},
    {  0, -11, -14,  14,  10, -10, 290, 340},
    { 14,   8,  14, -14, -10,  10, 290, 340},
    { 24, -11, -14,  14,  10, -10, 290, 340},
    { 16,   8,  14, -14, -10,  10, 290, 340},
    {  0, -11, -14,  14,  10, -10, 290, 340},
    {-14,   8,  14, -14, -10,  10, 290, 340},
    {-24, -11, -14,  14,  10, -10, 290, 340},
    {-14,   8,  14, -14, -10,  10, 290, 340},
    {  0,  12,  10,  10,   8,  -8, 420, 320},
};

// 2. Robot (~5.4 s): crisp mechanical pops one axis at a time with freezes
static constexpr DanceStep DANCE_ROBOT[] = {
    {-36,   0,  18, -18,  10,   0, 420, 360},
    {-36,  16,  18, -18,  10, -10, 420, 360},
    {  0,  16,   0,   0,   0,   0, 320, 360},
    { 36,  16, -18,  18,   0, -10, 420, 360},
    { 36, -12, -18,  18,  10, -10, 420, 360},
    {  0, -12,   0,   0,   0,   0, 320, 360},
    {  0,   0,   0,   0,   0,   0, 450, 360},
    { 32,   0, -20,  20,   0, -12, 400, 360},
    { 32,  14, -20,  20,  12, -12, 400, 360},
    {-32,  14,  20, -20,  12,   0, 420, 360},
    {-32, -10,  20, -20,  12, -12, 400, 360},
    {  0,   0,   0,   0,   0,   0, 400, 360},
    {  0,  20,  16,  16,  12, -12, 600, 340},
};

// 3. Sprinkler (~4.9 s): ticks across in little steps, then a fast whoosh back
static constexpr DanceStep DANCE_SPRINKLER[] = {
    { 40,   5,  14, -10,   8,  -8, 380, 340},
    { 20,   5,   8,  -6,   6,  -6, 260, 340},
    {  0,   5,   0,   0,   4,  -4, 260, 340},
    {-20,   5,  -8,   6,   6,  -6, 260, 340},
    {-40,   5, -14,  10,   8,  -8, 260, 340},
    { 40,  10,  16, -12,  12, -12, 550, 360},
    { 20,   5,   8,  -6,   6,  -6, 260, 340},
    {  0,   5,   0,   0,   4,  -4, 260, 340},
    {-20,   5,  -8,   6,   6,  -6, 260, 340},
    {-40,   5, -14,  10,   8,  -8, 260, 340},
    { 40,  10,  16, -12,  12, -12, 550, 360},
    { 15,   6,   6,  -6,   6,  -6, 260, 340},
    {-15,   6,  -6,   6,   6,  -6, 260, 340},
    {-40,   6, -14,  10,   8,  -8, 260, 340},
    { 35,  14,  18,  18,  12, -12, 550, 360},
};

// 4. Disco (~5.0 s): point high right / low left, rapid shimmy, big upward finish
static constexpr DanceStep DANCE_DISCO[] = {
    { 34,  20, -16,  24,   6, -14, 520, 320},
    {-30, -12,  24, -16,  14,  -6, 520, 320},
    { 34,  20, -16,  24,   6, -14, 520, 330},
    {-30, -12,  24, -16,  14,  -6, 520, 330},
    { 34,  20, -16,  24,   6, -14, 520, 340},
    {-30, -12,  24, -16,  14,  -6, 520, 340},
    { 10,  10,  12, -12,  10, -10, 190, 350},
    {-10,  10, -12,  12, -10,  10, 190, 350},
    { 10,  10,  12, -12,  10, -10, 190, 350},
    {-10,  10, -12,  12, -10,  10, 190, 350},
    { 10,  10,  12, -12,  10, -10, 190, 350},
    {-10,  10, -12,  12, -10,  10, 190, 350},
    {  0,  24,  22,  22,  14, -14, 750, 340},
};

struct DanceChoreo {
    const char* name;
    const DanceStep* steps;
    uint8_t stepCount;
};

static constexpr DanceChoreo DANCES[4] = {
    {"dance_groove",    DANCE_GROOVE,    (uint8_t)(sizeof(DANCE_GROOVE) / sizeof(DANCE_GROOVE[0]))},
    {"dance_robot",     DANCE_ROBOT,     (uint8_t)(sizeof(DANCE_ROBOT) / sizeof(DANCE_ROBOT[0]))},
    {"dance_sprinkler", DANCE_SPRINKLER, (uint8_t)(sizeof(DANCE_SPRINKLER) / sizeof(DANCE_SPRINKLER[0]))},
    {"dance_disco",     DANCE_DISCO,     (uint8_t)(sizeof(DANCE_DISCO) / sizeof(DANCE_DISCO[0]))},
};

static volatile GaitMode activeGait = GaitMode::IDLE;
static unsigned long gaitStartMs = 0;
static unsigned long gaitDurationMs = 0;
static uint8_t activeGestureId = GESTURE_NONE;
static uint8_t activeDanceIdx = 0;
static uint8_t lastDanceIdx = 255;
static int8_t curiousDir = 1;

struct MotionLock {
    bool locked = false;
    MotionLock() {
        if (motionMutex) {
            locked = (xSemaphoreTakeRecursive(motionMutex, pdMS_TO_TICKS(50)) == pdTRUE);
        }
    }
    ~MotionLock() {
        if (locked && motionMutex) {
            xSemaphoreGiveRecursive(motionMutex);
        }
    }
};

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

static void setTargetInternal(uint8_t ch, float deg, float speedDegSec) {
    if (ch >= MARVIN_SERVO_COUNT) return;
    float clamped = clampAngle(ch, deg);
    if (fabsf(clamped - targetDeg[ch]) > 0.05f || fabsf(clamped - currentDeg[ch]) > RELAX_TOLERANCE_DEG) {
        lastMovedMs[ch] = millis();
        channelRelaxed[ch] = false;
    }
    targetDeg[ch] = clamped;
    slewDegPerSec[ch] = speedDegSec;
}

static void setTarget(uint8_t ch, float deg, float speedDegSec = 180.0f) {
    MotionLock lock;
    if (assemblyMode) return;
    setTargetInternal(ch, deg, speedDegSec);
}

static void setHomePose(float speedDegSec = 140.0f) {
    MotionLock lock;
    activeGait = GaitMode::IDLE;
    activeGestureId = GESTURE_NONE;
    for (uint8_t i = 0; i < MARVIN_SERVO_COUNT; i++) {
        setTargetInternal(i, 90.0f, speedDegSec);
    }
}

static void setAssemblyMode(bool on) {
    MotionLock lock;
    assemblyMode = on;
    unsigned long now = millis();
    if (on) {
        activeGait = GaitMode::IDLE;
        activeGestureId = GESTURE_NONE;
        for (uint8_t i = 0; i < MARVIN_SERVO_COUNT; i++) {
            targetDeg[i] = 90.0f;
            slewDegPerSec[i] = 160.0f;
            lastMovedMs[i] = now;
            channelRelaxed[i] = false;
            writeServoDeg(i, currentDeg[i]);
        }
        Serial.println("[Motion] Assembly mode ON: all servos centered at 90 deg and held powered.");
    } else {
        for (uint8_t i = 0; i < MARVIN_SERVO_COUNT; i++) {
            lastMovedMs[i] = now;
        }
        Serial.println("[Motion] Assembly mode OFF: auto-relaxation re-enabled.");
    }
}

static bool isAssemblyMode() {
    return assemblyMode;
}

static bool areServosRelaxed() {
    if (assemblyMode || activeGait != GaitMode::IDLE) return false;
    for (uint8_t i = 0; i < MARVIN_SERVO_COUNT; i++) {
        if (!channelRelaxed[i]) return false;
    }
    return true;
}

static const char* getActiveDanceName() {
    if (activeDanceIdx < 4) return DANCES[activeDanceIdx].name;
    return "dance_groove";
}

static unsigned long computeDanceDurationMs(uint8_t danceIdx) {
    if (danceIdx >= 4) return 5000UL;
    unsigned long total = 0;
    const DanceChoreo& d = DANCES[danceIdx];
    for (uint8_t i = 0; i < d.stepCount; i++) {
        total += d.steps[i].durationMs;
    }
    return total;
}

static bool init() {
    if (!motionMutex) {
        motionMutex = xSemaphoreCreateRecursiveMutex();
    }
    MotionLock lock;
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
        Serial.println("[Motion] PCA9685 16-ch PWM driver initialized (50 Hz, auto-relax enabled).");
    }

    assemblyMode = false;
    unsigned long now = millis();
    for (uint8_t i = 0; i < MARVIN_SERVO_COUNT; i++) {
        currentDeg[i] = 90.0f;
        targetDeg[i] = 90.0f;
        slewDegPerSec[i] = 140.0f;
        lastMovedMs[i] = now;
        channelRelaxed[i] = false;
        writeServoDeg(i, 90.0f);
        delay(25);  // Stagger initial servo power-on to avoid surge
    }
    setHomePose(90.0f);
    return pcaPresent;
}

static void setHeadLook(float panDeg, float tiltDeg, float speedDegSec = 220.0f) {
    MotionLock lock;
    if (assemblyMode) return;
    setTargetInternal(CH_HEAD_PAN, panDeg, speedDegSec);
    setTargetInternal(CH_HEAD_TILT, tiltDeg, speedDegSec);
}

static void startDanceUnlocked(uint8_t danceIdx) {
    activeDanceIdx = danceIdx % 4;
    lastDanceIdx = activeDanceIdx;
    activeGait = GaitMode::DANCE_SEQ;
    activeGestureId = GESTURE_NONE;
    gaitStartMs = millis();
    gaitDurationMs = computeDanceDurationMs(activeDanceIdx);
    Serial.printf("[Motion] Starting %s (%lu ms)\n", DANCES[activeDanceIdx].name, gaitDurationMs);
}

static void triggerAction(const String& actionRaw, unsigned long durationMs = 2200) {
    MotionLock lock;
    if (assemblyMode) {
        Serial.printf("[Motion] Ignored action '%s' while assembly mode is ON.\n", actionRaw.c_str());
        return;
    }

    String act = actionRaw;
    act.toLowerCase();
    act.trim();

    gaitStartMs = millis();
    gaitDurationMs = durationMs;

    if (act == "dance") {
        uint8_t idx = (uint8_t)random(4);
        if (idx == lastDanceIdx) idx = (idx + 1 + (uint8_t)random(3)) % 4;
        startDanceUnlocked(idx);
    } else if (act == "dance_groove" || act == "groove") {
        startDanceUnlocked(0);
    } else if (act == "dance_robot" || act == "robot") {
        startDanceUnlocked(1);
    } else if (act == "dance_sprinkler" || act == "sprinkler") {
        startDanceUnlocked(2);
    } else if (act == "dance_disco" || act == "disco") {
        startDanceUnlocked(3);
    } else if (act == "step_forward" || act == "walk" || act == "waddle") {
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
        activeGestureId = GESTURE_NOD;
        gaitDurationMs = 1100;
    } else if (act == "shake") {
        activeGait = GaitMode::GESTURE_SEQ;
        activeGestureId = GESTURE_SHAKE;
        gaitDurationMs = 1200;
    } else if (act == "curious_tilt" || act == "curious") {
        activeGait = GaitMode::GESTURE_SEQ;
        activeGestureId = GESTURE_CURIOUS_TILT;
        curiousDir = -curiousDir;
        gaitDurationMs = 1200;
    } else if (act == "happy_bounce" || act == "bounce") {
        activeGait = GaitMode::GESTURE_SEQ;
        activeGestureId = GESTURE_HAPPY_BOUNCE;
        gaitDurationMs = 1100;
    } else if (act == "sad_droop" || act == "droop") {
        activeGait = GaitMode::GESTURE_SEQ;
        activeGestureId = GESTURE_SAD_DROOP;
        gaitDurationMs = 1400;
    } else if (act == "surprised_pop" || act == "pop") {
        activeGait = GaitMode::GESTURE_SEQ;
        activeGestureId = GESTURE_SURPRISED_POP;
        gaitDurationMs = 950;
    } else if (act == "sigh" || act == "crouch" || act == "brood") {
        activeGait = GaitMode::GESTURE_SEQ;
        activeGestureId = GESTURE_SIGH;
        gaitDurationMs = 2000;
    } else if (act == "fist_bump" || act == "wave") {
        activeGait = GaitMode::GESTURE_SEQ;
        activeGestureId = GESTURE_WAVE;
        gaitDurationMs = 2000;
    } else if (act == "bow") {
        activeGait = GaitMode::GESTURE_SEQ;
        activeGestureId = GESTURE_BOW;
        gaitDurationMs = 1800;
    } else if (act == "eyeroll" || act == "eye_roll") {
        activeGait = GaitMode::GESTURE_SEQ;
        activeGestureId = GESTURE_EYEROLL;
        gaitDurationMs = 1900;
    } else if (act == "stand_tall" || act == "home" || act == "center") {
        setHomePose(180.0f);
    }
}

// Call periodically (~50 Hz / every 20 ms)
static void tick(uint32_t dtMs) {
    MotionLock lock;
    unsigned long now = millis();
    float dt = (float)dtMs / 1000.0f;
    if (dt <= 0.0f || dt > 0.25f) dt = 0.02f;

    // Assembly mode: drive all servos to 90 deg center and hold continuously powered
    if (assemblyMode) {
        activeGait = GaitMode::IDLE;
        activeGestureId = GESTURE_NONE;
        for (uint8_t i = 0; i < MARVIN_SERVO_COUNT; i++) {
            targetDeg[i] = 90.0f;
            float diff = targetDeg[i] - currentDeg[i];
            float maxStep = 160.0f * dt;
            if (fabsf(diff) <= maxStep) {
                currentDeg[i] = targetDeg[i];
            } else {
                currentDeg[i] += (diff > 0.0f ? maxStep : -maxStep);
            }
            lastMovedMs[i] = now;
            channelRelaxed[i] = false;
            writeServoDeg(i, currentDeg[i]);
        }
        return;
    }

    unsigned long elapsed = now - gaitStartMs;

    if (activeGait != GaitMode::IDLE) {
        if (elapsed >= gaitDurationMs) {
            setHomePose(160.0f);
        } else {
            float phase = (float)elapsed / 1000.0f;  // seconds
            float w = 2.0f * PI * 1.15f * phase;     // ~1.15 Hz gentle waddle

            if (activeGait == GaitMode::DANCE_SEQ) {
                const DanceChoreo& d = DANCES[activeDanceIdx % 4];
                unsigned long acc = 0;
                uint8_t stepIdx = d.stepCount - 1;
                for (uint8_t k = 0; k < d.stepCount; k++) {
                    acc += d.steps[k].durationMs;
                    if (elapsed < acc) {
                        stepIdx = k;
                        break;
                    }
                }
                const DanceStep& st = d.steps[stepIdx];
                float spd = (float)st.slewDegSec;
                setTargetInternal(CH_HEAD_PAN, 90.0f + st.panOff, spd);
                setTargetInternal(CH_HEAD_TILT, 90.0f + st.tiltOff, spd);
                setTargetInternal(CH_L_SHOULDER_PITCH, 90.0f + st.lShPitchOff, spd * 0.9f);
                setTargetInternal(CH_R_SHOULDER_PITCH, 90.0f + st.rShPitchOff, spd * 0.9f);
                setTargetInternal(CH_L_SHOULDER_ROLL, 90.0f + st.lShRollOff, spd * 0.85f);
                setTargetInternal(CH_R_SHOULDER_ROLL, 90.0f + st.rShRollOff, spd * 0.85f);
            } else if (activeGait == GaitMode::WADDLE_FWD || activeGait == GaitMode::WADDLE_REV) {
                float dir = (activeGait == GaitMode::WADDLE_FWD) ? 1.0f : -1.0f;
                float roll = sinf(w) * 14.0f;        // Ankle side-to-side weight shift
                float step = cosf(w) * 14.0f * dir;  // Hip pitch forward/back stride
                setTargetInternal(CH_L_ANKLE_ROLL, 90.0f + roll, 240.0f);
                setTargetInternal(CH_R_ANKLE_ROLL, 90.0f + roll, 240.0f);
                setTargetInternal(CH_L_HIP_PITCH, 90.0f + step, 240.0f);
                setTargetInternal(CH_R_HIP_PITCH, 90.0f - step, 240.0f);
                setTargetInternal(CH_L_SHOULDER_PITCH, 90.0f - step * 1.2f, 220.0f);
                setTargetInternal(CH_R_SHOULDER_PITCH, 90.0f + step * 1.2f, 220.0f);
                setTargetInternal(CH_L_SHOULDER_ROLL, 90.0f + max(0.0f, -roll) * 0.8f, 200.0f);
                setTargetInternal(CH_R_SHOULDER_ROLL, 90.0f - max(0.0f, roll) * 0.8f, 200.0f);
            } else if (activeGait == GaitMode::TURN_LEFT || activeGait == GaitMode::TURN_RIGHT) {
                float turnDir = (activeGait == GaitMode::TURN_LEFT) ? 1.0f : -1.0f;
                float roll = sinf(w) * 15.0f;
                float step = cosf(w) * 12.0f * turnDir;
                setTargetInternal(CH_L_ANKLE_ROLL, 90.0f + roll, 240.0f);
                setTargetInternal(CH_R_ANKLE_ROLL, 90.0f + roll, 240.0f);
                setTargetInternal(CH_L_HIP_PITCH, 90.0f + step, 240.0f);
                setTargetInternal(CH_R_HIP_PITCH, 90.0f + step, 240.0f);
            } else if (activeGait == GaitMode::GESTURE_SEQ) {
                float p = (gaitDurationMs > 0) ? ((float)elapsed / (float)gaitDurationMs) : 1.0f;
                if (activeGestureId == GESTURE_NOD) {
                    float d = sinf(p * 2.0f * PI * 2.0f) * 14.0f;
                    setTargetInternal(CH_HEAD_TILT, 90.0f + d, 260.0f);
                } else if (activeGestureId == GESTURE_SHAKE) {
                    float d = sinf(p * 2.0f * PI * 2.0f) * 22.0f;
                    setTargetInternal(CH_HEAD_PAN, 90.0f + d, 290.0f);
                } else if (activeGestureId == GESTURE_CURIOUS_TILT) {
                    if (p < 0.72f) {
                        setTargetInternal(CH_HEAD_PAN, 90.0f + (float)curiousDir * 14.0f, 190.0f);
                        setTargetInternal(CH_HEAD_TILT, 99.0f, 180.0f);
                        setTargetInternal(CH_L_SHOULDER_ROLL, 90.0f + (float)curiousDir * 8.0f, 160.0f);
                    } else {
                        setTargetInternal(CH_HEAD_PAN, 90.0f, 180.0f);
                        setTargetInternal(CH_HEAD_TILT, 90.0f, 180.0f);
                        setTargetInternal(CH_L_SHOULDER_ROLL, 90.0f, 160.0f);
                    }
                } else if (activeGestureId == GESTURE_HAPPY_BOUNCE) {
                    float bounce = fabsf(sinf(p * 2.0f * PI)) * 11.0f;
                    float sway = sinf(p * 2.0f * PI) * 8.0f;
                    setTargetInternal(CH_HEAD_TILT, 90.0f + bounce, 280.0f);
                    setTargetInternal(CH_HEAD_PAN, 90.0f + sway, 260.0f);
                    setTargetInternal(CH_L_SHOULDER_PITCH, 90.0f + bounce, 240.0f);
                    setTargetInternal(CH_R_SHOULDER_PITCH, 90.0f + bounce, 240.0f);
                } else if (activeGestureId == GESTURE_SAD_DROOP) {
                    if (p < 0.70f) {
                        setTargetInternal(CH_HEAD_TILT, 73.0f, 95.0f);
                        setTargetInternal(CH_L_SHOULDER_PITCH, 78.0f, 85.0f);
                        setTargetInternal(CH_R_SHOULDER_PITCH, 78.0f, 85.0f);
                    } else {
                        setTargetInternal(CH_HEAD_TILT, 90.0f, 110.0f);
                        setTargetInternal(CH_L_SHOULDER_PITCH, 90.0f, 110.0f);
                        setTargetInternal(CH_R_SHOULDER_PITCH, 90.0f, 110.0f);
                    }
                } else if (activeGestureId == GESTURE_SURPRISED_POP) {
                    if (p < 0.45f) {
                        setTargetInternal(CH_HEAD_TILT, 112.0f, 340.0f);
                        setTargetInternal(CH_HEAD_PAN, 96.0f, 300.0f);
                        setTargetInternal(CH_L_SHOULDER_PITCH, 104.0f, 300.0f);
                        setTargetInternal(CH_R_SHOULDER_PITCH, 104.0f, 300.0f);
                    } else {
                        setTargetInternal(CH_HEAD_TILT, 90.0f, 200.0f);
                        setTargetInternal(CH_HEAD_PAN, 90.0f, 200.0f);
                        setTargetInternal(CH_L_SHOULDER_PITCH, 90.0f, 200.0f);
                        setTargetInternal(CH_R_SHOULDER_PITCH, 90.0f, 200.0f);
                    }
                } else if (activeGestureId == GESTURE_SIGH) {
                    setTargetInternal(CH_HEAD_TILT, 68.0f, 90.0f);
                    setTargetInternal(CH_L_SHOULDER_PITCH, 74.0f, 80.0f);
                    setTargetInternal(CH_R_SHOULDER_PITCH, 74.0f, 80.0f);
                } else if (activeGestureId == GESTURE_WAVE) {
                    float wave = sinf(phase * 2.0f * PI * 1.8f) * 18.0f;
                    setTargetInternal(CH_R_SHOULDER_PITCH, 140.0f, 240.0f);
                    setTargetInternal(CH_R_SHOULDER_ROLL, 115.0f + wave, 260.0f);
                } else if (activeGestureId == GESTURE_BOW) {
                    setTargetInternal(CH_HEAD_TILT, 68.0f, 120.0f);
                    setTargetInternal(CH_L_HIP_PITCH, 108.0f, 120.0f);
                    setTargetInternal(CH_R_HIP_PITCH, 108.0f, 120.0f);
                    setTargetInternal(CH_L_SHOULDER_PITCH, 75.0f, 120.0f);
                    setTargetInternal(CH_R_SHOULDER_PITCH, 75.0f, 120.0f);
                } else if (activeGestureId == GESTURE_EYEROLL) {
                    if (p < 0.25f) {
                        setTargetInternal(CH_HEAD_TILT, 108.0f, 180.0f);
                        setTargetInternal(CH_HEAD_PAN, 72.0f, 160.0f);
                    } else if (p < 0.70f) {
                        setTargetInternal(CH_HEAD_TILT, 112.0f, 180.0f);
                        setTargetInternal(CH_HEAD_PAN, 108.0f, 160.0f);
                    } else {
                        setTargetInternal(CH_HEAD_TILT, 72.0f, 140.0f);
                        setTargetInternal(CH_HEAD_PAN, 90.0f, 140.0f);
                    }
                }
            }
        }
    }

    // Keep the coupled head axis powered while the other head axis is actively moving
    bool headAxisMoving =
        (fabsf(targetDeg[CH_HEAD_PAN] - currentDeg[CH_HEAD_PAN]) > RELAX_TOLERANCE_DEG) ||
        (fabsf(targetDeg[CH_HEAD_TILT] - currentDeg[CH_HEAD_TILT]) > RELAX_TOLERANCE_DEG);

    for (uint8_t i = 0; i < MARVIN_SERVO_COUNT; i++) {
        float diff = targetDeg[i] - currentDeg[i];
        bool moving = fabsf(diff) > RELAX_TOLERANCE_DEG;

        if (fabsf(diff) > 0.01f) {
            float maxStep = slewDegPerSec[i] * dt;
            if (fabsf(diff) <= maxStep) {
                currentDeg[i] = targetDeg[i];
            } else {
                currentDeg[i] += (diff > 0.0f ? maxStep : -maxStep);
            }
        }

        if (moving) {
            lastMovedMs[i] = now;
            channelRelaxed[i] = false;
            writeServoDeg(i, currentDeg[i]);
        } else {
            bool coupledHold = headAxisMoving && (i == CH_HEAD_PAN || i == CH_HEAD_TILT);
            bool inSafeBand = true;
            if (i == CH_HEAD_TILT) {
                inSafeBand = (currentDeg[i] >= TILT_RELAX_MIN_DEG && currentDeg[i] <= TILT_RELAX_MAX_DEG);
            }
            bool canRelax = (activeGait == GaitMode::IDLE) &&
                            !coupledHold &&
                            inSafeBand &&
                            ((now - lastMovedMs[i]) >= RELAX_AFTER_MS);

            if (canRelax) {
                if (!channelRelaxed[i]) {
                    pcaSetPWM(i, 0, 4096);  // PCA9685 full-off: silences MG90S hum, heat & battery drain
                    channelRelaxed[i] = true;
                }
            } else {
                if (coupledHold) {
                    lastMovedMs[i] = now;
                }
                channelRelaxed[i] = false;
                writeServoDeg(i, currentDeg[i]);
            }
        }
    }
}

}  // namespace marvin_motion
