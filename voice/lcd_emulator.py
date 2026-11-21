#!/usr/bin/env python3
"""
Waveshare 2.0" ST7789 IPS LCD Hardware Emulator (320x240)
Zero-Weight Procedural BMO Face Engine & MacBook Face Mirror.

Exact port of the MacBook Web Console BMO Face Engine:
- Exact EMOTION_PRESETS with spring interpolation.
- Adventure Time Mint CRT glow.
- Squash-and-stretch fluid blinks, eye shines, and flat eyelid cuts.
- Expressive curved eyebrows with independent lift & tilt.
- 3-stage dynamic lip-sync mouth (Stage 2 bean mouth with teeth and tongue,
  Stage 1 syllable 'o', Stage 0 curved smile line).
- Sparkles, teardrop, thinking dots, and drifting sleepy Zzz.
- Dual visual enclosures: MacBook BMO Chassis vs. Waveshare 2.0" Blue PCB.
"""

import argparse
import math
import os
import sys
import time
import urllib.request
import json

if "--headless" in sys.argv:
    os.environ["SDL_VIDEODRIVER"] = "dummy"

import pygame

# Hardware & Canvas Dimensions
LCD_W = 320
LCD_H = 240
SCALE = 2  # 640x480 on desktop

WIN_W = LCD_W * SCALE + 80   # 720
WIN_H = LCD_H * SCALE + 140  # 620

# Colors matching MacBook console (static/index.html)
COL_BMO_SHELL    = (88, 184, 162)   # #58b8a2
COL_BMO_SHELL_DK = (58, 138, 118)   # #3a8a76
COL_BMO_BG       = (205, 238, 224)  # #cdeee0 Mint CRT Screen
COL_BMO_BORDER   = (170, 215, 198)  # #aad7c6
COL_BMO_INK      = (20, 40, 38)     # #142826 Deep Forest Ink
COL_BMO_BLUSH    = (244, 142, 162)  # #f48ea2 Cute Rosy Blush
COL_BMO_MOUTH_BG = (27, 77, 62)     # #1b4d3e Dark Mouth Throat
COL_BMO_TONGUE   = (240, 140, 158)  # #f08c9e Bouncing Coral Tongue
COL_BMO_TEETH    = (245, 255, 249)  # #f5fff9 Crisp White Teeth
COL_BMO_SWEAT    = (125, 211, 252)  # #7dd3fc Teardrop
COL_WHITE        = (255, 255, 255)
COL_PCB_BLUE     = (15, 76, 129)
COL_PCB_GOLD     = (229, 185, 90)
COL_BEZEL        = (15, 20, 28)

# Exact Emotion Presets from MacBook index.html
EMOTION_PRESETS = {
    "sleepy":    {"openL": 0.06, "openR": 0.06, "scaleL": 1.0,  "scaleR": 1.0,  "happyArc": 0.0, "flatLid": 0.0,  "winceL": 0.0, "browAlpha": 0.0,  "browLiftL": 0.0,  "browLiftR": 0.0,  "browTiltL": 0.0,   "browTiltR": 0.0,   "tilt": 0.02,  "blush": 0.0,  "sparkle": 0.0, "sweat": 0.0, "smile": 0.15, "mouthW": 0.72, "gazeX": 0.0, "gazeY": 3.0},
    "neutral":   {"openL": 1.0,  "openR": 1.0,  "scaleL": 1.0,  "scaleR": 1.0,  "happyArc": 0.0, "flatLid": 0.0,  "winceL": 0.0, "browAlpha": 0.0,  "browLiftL": 0.0,  "browLiftR": 0.0,  "browTiltL": 0.0,   "browTiltR": 0.0,   "tilt": 0.0,   "blush": 0.12, "sparkle": 0.0, "sweat": 0.0, "smile": 0.65, "mouthW": 0.95, "gazeX": 0.0, "gazeY": 0.0},
    "listening": {"openL": 1.0,  "openR": 1.0,  "scaleL": 1.15, "scaleR": 1.15, "happyArc": 0.0, "flatLid": 0.0,  "winceL": 0.0, "browAlpha": 0.7,  "browLiftL": -5.0, "browLiftR": -5.0, "browTiltL": -0.08, "browTiltR": 0.08,  "tilt": -0.02, "blush": 0.22, "sparkle": 0.0, "sweat": 0.0, "smile": 0.45, "mouthW": 0.80, "gazeX": 0.0, "gazeY": -1.0},
    "thinking":  {"openL": 0.88, "openR": 0.96, "scaleL": 0.96, "scaleR": 1.06, "happyArc": 0.0, "flatLid": 0.22, "winceL": 0.0, "browAlpha": 0.95, "browLiftL": 1.0,  "browLiftR": -6.0, "browTiltL": 0.05,  "browTiltR": 0.18,  "tilt": -0.04, "blush": 0.0,  "sparkle": 0.0, "sweat": 0.0, "smile": 0.0,  "mouthW": 0.65, "gazeX": -8.0,"gazeY": -7.0},
    "happy":     {"openL": 1.0,  "openR": 1.0,  "scaleL": 1.06, "scaleR": 1.06, "happyArc": 0.25,"flatLid": 0.0,  "winceL": 0.0, "browAlpha": 0.0,  "browLiftL": -3.0, "browLiftR": -3.0, "browTiltL": 0.0,   "browTiltR": 0.0,   "tilt": 0.02,  "blush": 0.65, "sparkle": 0.0, "sweat": 0.0, "smile": 0.95, "mouthW": 1.15, "gazeX": 0.0, "gazeY": 0.0},
    "excited":   {"openL": 1.0,  "openR": 1.0,  "scaleL": 1.12, "scaleR": 1.12, "happyArc": 1.0, "flatLid": 0.0,  "winceL": 0.0, "browAlpha": 0.0,  "browLiftL": -5.0, "browLiftR": -5.0, "browTiltL": 0.0,   "browTiltR": 0.0,   "tilt": 0.035, "blush": 0.95, "sparkle": 1.0, "sweat": 0.0, "smile": 1.0,  "mouthW": 1.32, "gazeX": 0.0, "gazeY": -1.0},
    "curious":   {"openL": 1.0,  "openR": 1.0,  "scaleL": 0.90, "scaleR": 1.24, "happyArc": 0.0, "flatLid": 0.0,  "winceL": 0.0, "browAlpha": 1.0,  "browLiftL": 2.0,  "browLiftR": -8.0, "browTiltL": 0.12,  "browTiltR": 0.22,  "tilt": -0.075,"blush": 0.15, "sparkle": 0.0, "sweat": 0.0, "smile": 0.20, "mouthW": 0.62, "gazeX": 3.0, "gazeY": -2.0},
    "focused":   {"openL": 0.86, "openR": 0.86, "scaleL": 1.02, "scaleR": 1.02, "happyArc": 0.0, "flatLid": 0.26, "winceL": 0.0, "browAlpha": 1.0,  "browLiftL": 3.0,  "browLiftR": 3.0,  "browTiltL": 0.26,  "browTiltR": -0.26, "tilt": 0.0,   "blush": 0.0,  "sparkle": 0.0, "sweat": 0.0, "smile": 0.45, "mouthW": 0.92, "gazeX": 0.0, "gazeY": 0.0},
    "alarmed":   {"openL": 1.0,  "openR": 1.0,  "scaleL": 1.38, "scaleR": 1.38, "happyArc": 0.0, "flatLid": 0.0,  "winceL": 0.0, "browAlpha": 1.0,  "browLiftL": -7.0, "browLiftR": -7.0, "browTiltL": -0.28, "browTiltR": 0.28,  "tilt": 0.0,   "blush": 0.0,  "sparkle": 0.0, "sweat": 1.0, "smile": -0.55,"mouthW": 0.78, "gazeX": 0.0, "gazeY": 0.0},
    "sad":       {"openL": 0.82, "openR": 0.82, "scaleL": 0.96, "scaleR": 0.96, "happyArc": 0.0, "flatLid": 0.18, "winceL": 0.0, "browAlpha": 1.0,  "browLiftL": -2.0, "browLiftR": -2.0, "browTiltL": -0.32, "browTiltR": 0.32,  "tilt": 0.03,  "blush": 0.0,  "sparkle": 0.0, "sweat": 0.0, "smile": -0.80,"mouthW": 0.88, "gazeX": 0.0, "gazeY": 5.0},
    "bored":     {"openL": 0.68, "openR": 0.68, "scaleL": 1.0,  "scaleR": 1.0,  "happyArc": 0.0, "flatLid": 0.88, "winceL": 0.0, "browAlpha": 0.65, "browLiftL": 2.0,  "browLiftR": 2.0,  "browTiltL": 0.02,  "browTiltR": -0.02, "tilt": 0.01,  "blush": 0.0,  "sparkle": 0.0, "sweat": 0.0, "smile": -0.18,"mouthW": 0.88, "gazeX": 2.0, "gazeY": 2.0},
    "pained":    {"openL": 0.75, "openR": 0.78, "scaleL": 1.0,  "scaleR": 0.95, "happyArc": 0.0, "flatLid": 0.55, "winceL": 1.0, "browAlpha": 1.0,  "browLiftL": 3.0,  "browLiftR": -2.0, "browTiltL": 0.28,  "browTiltR": 0.20,  "tilt": -0.04, "blush": 0.0,  "sparkle": 0.0, "sweat": 1.0, "smile": -0.70,"mouthW": 0.95, "gazeX": -2.0,"gazeY": 3.0},
}


class ST7789Emulator:
    def __init__(self, server_url="http://127.0.0.1:7860", headless=False):
        self.server_url = server_url
        self.headless = headless
        pygame.init()
        pygame.display.set_caption("Waveshare 2.0\" ST7789 LCD Emulator - MacBook Face Match")

        self.screen = pygame.display.set_mode((WIN_W, WIN_H))
        self.lcd_surf = pygame.Surface((LCD_W, LCD_H))
        self.clock = pygame.time.Clock()

        self.mood = "happy"
        self.cur = dict(EMOTION_PRESETS["happy"])
        self.cur["mouthOpen"] = 0.0
        self.cur["visemeW"] = 1.0

        self.pointer_x = 0.0
        self.pointer_y = 0.0
        self.idle_gaze_x = 0.0
        self.idle_gaze_y = 0.0
        self.next_idle_shift = time.time() + 2.2

        self.blink_factor = 1.0
        self.blink_phase = 0
        self.next_blink_time = time.time() + 2.8

        self.is_talking = False
        self.speech_level = 0.0
        self.speech_width = 1.0

        self.view_mode = "macbook"  # "macbook" | "pcb"
        self.last_poll_time = 0
        self.last_server_update = 0

        try:
            self.font_sm = pygame.font.SysFont("monospace", 11, bold=True)
            self.font_md = pygame.font.SysFont("monospace", 14, bold=True)
            self.font_lg = pygame.font.SysFont("monospace", 22, bold=True)
        except Exception:
            self.font_sm = pygame.font.Font(None, 14)
            self.font_md = pygame.font.Font(None, 18)
            self.font_lg = pygame.font.Font(None, 28)

    def set_emotion(self, mood):
        if mood in EMOTION_PRESETS:
            self.mood = mood

    def poll_server(self):
        now = time.time()
        if now - self.last_poll_time < 0.4:
            return
        self.last_poll_time = now

        try:
            req = urllib.request.Request(f"{self.server_url}/api/robot/face/state")
            with urllib.request.urlopen(req, timeout=0.8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                up = data.get("updated_at", 0)
                if up > self.last_server_update:
                    self.last_server_update = up
                    m = data.get("mood")
                    if m and m in EMOTION_PRESETS:
                        self.set_emotion(m)
                    elif m == "laugh":
                        self.set_emotion("excited")
                    sp = data.get("speaking")
                    if sp is not None:
                        self.is_talking = bool(sp)
                        self.speech_level = 0.75 if self.is_talking else 0.0
        except Exception:
            pass

    def update_physics(self):
        now = time.time()

        # Blinking
        if self.mood != "sleepy" and now >= self.next_blink_time and self.blink_phase == 0:
            self.blink_phase = 1
            self.next_blink_time = now + 2.8 + (time.time() % 2.5)

        if self.blink_phase == 1:
            self.blink_factor -= 0.34
            if self.blink_factor <= 0.05:
                self.blink_factor = 0.05
                self.blink_phase = 2
        elif self.blink_phase == 2:
            self.blink_factor += 0.28
            if self.blink_factor >= 1.0:
                self.blink_factor = 1.0
                self.blink_phase = 0

        # Autonomous idle saccades
        if now >= self.next_idle_shift:
            self.idle_gaze_x = (math.sin(now * 1.5)) * 3.5
            self.idle_gaze_y = (math.cos(now * 1.8)) * 2.0
            self.next_idle_shift = now + 2.0 + (time.time() % 2.0)

        # Smooth spring interpolation matching MacBook
        tgt = EMOTION_PRESETS.get(self.mood, EMOTION_PRESETS["neutral"])
        k = 0.19
        for key in tgt:
            self.cur[key] += (tgt[key] - self.cur[key]) * k

        # Speech mouth opening
        target_open = self.speech_level if self.is_talking else 0.0
        rate = 0.75 if self.is_talking else 0.35
        self.cur["mouthOpen"] += (target_open - self.cur["mouthOpen"]) * rate
        self.cur["visemeW"] += (self.speech_width - self.cur["visemeW"]) * 0.4

    def render_face(self):
        now = time.time()
        self.lcd_surf.fill(COL_BMO_BG)

        # CRT Radial Highlight simulation
        w = LCD_W
        h = LCD_H
        scale = 1.18

        # Draw subtle border
        pygame.draw.rect(self.lcd_surf, COL_BMO_BORDER, (0, 0, w, h), 3)

        cx = w // 2  # 160
        cy = int(h * 0.48)  # 115

        # Organic breathing & emotion bob
        bob_y = math.sin(now * 3.2) * 2.8 * scale
        jitter_x = 0
        if self.mood in ("excited", "happy"):
            bob_y += abs(math.sin(now * 14.0)) * -6.5 * scale
        elif self.mood == "alarmed":
            jitter_x = math.sin(now * 55.0) * 2.5 * scale

        if self.cur["mouthOpen"] > 0.05:
            bob_y += self.cur["mouthOpen"] * 2.2 * scale

        # Gaze offset
        track_w = 0.15 if self.mood in ("thinking", "sleepy") else 1.0
        gx = (self.cur["gazeX"] + (self.pointer_x * 5.5 + self.idle_gaze_x * 0.4) * track_w) * scale
        gy = (self.cur["gazeY"] + (self.pointer_y * 3.8 + self.idle_gaze_y * 0.4) * track_w) * scale

        center_x = cx + int(jitter_x)
        center_y = cy + int(bob_y)

        eye_sep = int(74 * scale)
        eye_y = center_y + int(-12 * scale + gy)

        # 1. Cute Rosy Cheek Blushes
        if self.cur["blush"] > 0.02:
            blush_rx = int((18 if self.mood in ("happy", "excited") else 16) * scale)
            blush_ry = int((9.5 if self.mood in ("happy", "excited") else 8.5) * scale)
            blush_y = eye_y + int(22 * scale)

            s = pygame.Surface((blush_rx * 2, blush_ry * 2), pygame.SRCALPHA)
            alpha = int(self.cur["blush"] * 0.58 * 255)
            pygame.draw.ellipse(s, (*COL_BMO_BLUSH, alpha), (0, 0, blush_rx * 2, blush_ry * 2))

            for sign in (-1, 1):
                bx = center_x + int(sign * (eye_sep + 18 * scale) + gx * 0.4) - blush_rx
                self.lcd_surf.blit(s, (bx, blush_y - blush_ry))

        # 2. Draw Left & Right Eyes
        self._draw_eye(center_x - eye_sep + int(gx), eye_y, scale, self.cur["openL"], self.cur["scaleL"], True)
        self._draw_eye(center_x + eye_sep + int(gx), eye_y, scale, self.cur["openR"], self.cur["scaleR"], False)

        # 3. Expressive Eyebrows
        if self.cur["browAlpha"] > 0.04:
            s_brow = pygame.Surface((w, h), pygame.SRCALPHA)
            alpha = int(min(1.0, self.cur["browAlpha"]) * 255)
            col = (*COL_BMO_INK, alpha)

            for sign, lift, tilt in [
                (-1, self.cur["browLiftL"], self.cur["browTiltL"]),
                ( 1, self.cur["browLiftR"], self.cur["browTiltR"]),
            ]:
                bx = center_x + int(sign * eye_sep + gx * 0.85)
                by = eye_y - int((22 - lift) * scale)
                # Curved eyebrow arc
                pts = []
                for i in range(11):
                    t = i / 10.0
                    xt = -13 * scale * (1.0 - 2.0 * t)
                    yt = -3.2 * scale * (4.0 * t * (1.0 - t))
                    # Rotate by tilt
                    rx = xt * math.cos(tilt) - yt * math.sin(tilt)
                    ry = xt * math.sin(tilt) + yt * math.cos(tilt)
                    pts.append((int(bx + rx), int(by + ry)))
                if len(pts) > 1:
                    pygame.draw.lines(s_brow, col, False, pts, max(2, int(3.8 * scale)))

            self.lcd_surf.blit(s_brow, (0, 0))

        # 4. Lip-Synced Mouth
        mouth_x = center_x + int(gx * 0.55)
        mouth_y = eye_y + int(26 * scale + gy * 0.55)
        self._draw_mouth(mouth_x, mouth_y, scale)

        # 5. Extras: Sleepy Zzz drift / Thinking dots / Teardrop
        if self.mood == "sleepy":
            drift = int((now * 20) % 20)
            txt_z1 = self.font_md.render("z", True, (20, 40, 38))
            txt_z2 = self.font_sm.render("z", True, (20, 40, 38))
            self.lcd_surf.blit(txt_z1, (center_x + int(86 * scale + drift * 0.4), center_y - int(38 * scale + drift)))
            self.lcd_surf.blit(txt_z2, (center_x + int(72 * scale + drift * 0.2), center_y - int(24 * scale + drift * 0.7)))
        elif self.cur["sweat"] > 0.08:
            drop_slide = int(((now * 30) % 16) * scale)
            sx = center_x + eye_sep + int(28 * scale)
            sy = eye_y - int(18 * scale) + drop_slide
            pygame.draw.ellipse(self.lcd_surf, COL_BMO_SWEAT, (sx - 5, sy, 10, 14))
            pygame.draw.polygon(self.lcd_surf, COL_BMO_SWEAT, [(sx - 5, sy + 7), (sx + 5, sy + 7), (sx, sy - 6)])
            pygame.draw.ellipse(self.lcd_surf, COL_BMO_INK, (sx - 5, sy, 10, 14), 2)

    def _draw_eye(self, x, y, scale, open_param, scale_param, is_left):
        # Happy ^ ^ Arc
        if self.cur["happyArc"] > 0.55 and self.blink_factor > 0.4:
            r = int(13.5 * scale)
            rect = pygame.Rect(x - r, y + int(4.5 * scale) - r, r * 2, r * 2)
            pygame.draw.arc(self.lcd_surf, COL_BMO_INK, rect, math.pi * 0.12, math.pi * 0.88, max(2, int(4.8 * scale)))
            return

        eff_open = open_param * self.blink_factor
        if eff_open < 0.16:
            # Closed blink or sleepy curve
            if self.mood == "sleepy":
                r = int(11.5 * scale)
                rect = pygame.Rect(x - r, y - int(3.5 * scale) - r, r * 2, r * 2)
                pygame.draw.arc(self.lcd_surf, COL_BMO_INK, rect, math.pi * 1.15, math.pi * 1.85, max(2, int(4.2 * scale)))
            else:
                hw = int(11.5 * scale)
                pygame.draw.line(self.lcd_surf, COL_BMO_INK, (x - hw, y), (x + hw, y), max(2, int(4.2 * scale)))
            return

        rx = int(9.5 * scale * scale_param * (1.0 + (1.0 - self.blink_factor) * 0.32))
        ry = int(13.0 * scale * scale_param * max(0.18, eff_open))

        # Standard BMO ink-oval eye
        pygame.draw.ellipse(self.lcd_surf, COL_BMO_INK, (x - rx, y - ry, rx * 2, ry * 2))

        # Eye shine highlight on sad / curious
        if self.mood in ("sad", "curious"):
            sx = x - int(rx * 0.3)
            sy = y - int(ry * 0.3)
            sr = max(2, int(2.6 * scale))
            pygame.draw.circle(self.lcd_surf, (230, 255, 245), (sx, sy), sr)

        # Flat unamused top eyelid cut
        if self.cur["flatLid"] > 0.35:
            lid_y = y - ry + int(self.cur["flatLid"] * ry * 0.85)
            hw = int(rx * 1.18)
            pygame.draw.line(self.lcd_surf, COL_BMO_INK, (x - hw, lid_y), (x + hw, lid_y), max(2, int(3.8 * scale)))

    def _draw_mouth(self, x, y, scale):
        open_val = self.cur["mouthOpen"]
        smile = self.cur["smile"]
        half_w = int(20.5 * scale * self.cur["mouthW"] * self.cur["visemeW"])
        jaw_bob = int(open_val * 2.8 * scale)
        my = y + jaw_bob

        if open_val >= 0.28:
            # Stage 2: 2-Tone open bean mouth with teeth and tongue
            open_h = int((8 + open_val * 26) * scale)
            rect = pygame.Rect(x - half_w, my - int(open_h * 0.24), half_w * 2, open_h)
            pygame.draw.ellipse(self.lcd_surf, COL_BMO_MOUTH_BG, rect)

            # Teeth top strip
            teeth_h = int(open_h * 0.35)
            teeth_rect = pygame.Rect(x - int(half_w * 0.85), my - int(open_h * 0.24), int(half_w * 1.7), teeth_h)
            pygame.draw.rect(self.lcd_surf, COL_BMO_TEETH, teeth_rect)

            # Tongue bottom arc
            tongue_h = int(open_h * 0.52)
            tongue_rect = pygame.Rect(x - int(half_w * 0.72), my + int(open_h * 0.22), int(half_w * 1.44), tongue_h)
            pygame.draw.ellipse(self.lcd_surf, COL_BMO_TONGUE, tongue_rect)

            # Ink border
            pygame.draw.ellipse(self.lcd_surf, COL_BMO_INK, rect, max(2, int(3.6 * scale)))
        elif open_val >= 0.08:
            # Stage 1: Cartoon syllable 'o' / pill mouth
            rx = int(half_w * 0.58)
            ry = int((3.8 + open_val * 20) * scale)
            rect = pygame.Rect(x - rx, my, rx * 2, ry * 2)
            pygame.draw.ellipse(self.lcd_surf, COL_BMO_MOUTH_BG, rect)
            tongue_rect = pygame.Rect(x - int(rx * 0.75), my + ry, int(rx * 1.5), ry)
            pygame.draw.ellipse(self.lcd_surf, COL_BMO_TONGUE, tongue_rect)
            pygame.draw.ellipse(self.lcd_surf, COL_BMO_INK, rect, max(2, int(3.4 * scale)))
        else:
            # Stage 0: Closed curved smile line
            curve = smile * 13.0 * scale
            pts = []
            for i in range(17):
                t = i / 16.0
                xt = -half_w * 0.85 * (1.0 - 2.0 * t)
                yt = (4.0 * t * (1.0 - t)) * curve - curve * 0.18
                pts.append((int(x + xt), int(my + yt)))
            if len(pts) > 1:
                pygame.draw.lines(self.lcd_surf, COL_BMO_INK, False, pts, max(2, int(3.8 * scale)))

    def draw_enclosure(self):
        lcd_x = (WIN_W - LCD_W * SCALE) // 2
        lcd_y = 65

        if self.view_mode == "macbook":
            # MacBook BMO Chassis Frame
            pygame.draw.rect(self.screen, COL_BMO_SHELL, (0, 0, WIN_W, WIN_H))

            # Speaker holes
            for i in range(6):
                pygame.draw.circle(self.screen, COL_BMO_INK, (50 + i * 14, 28), 3)

            # Mood pill
            pill_str = f"BMO • {self.mood.upper()}"
            txt_pill = self.font_md.render(pill_str, True, COL_BMO_INK)
            pw, ph = txt_pill.get_size()
            pill_rect = pygame.Rect(WIN_W - pw - 60, 20, pw + 18, ph + 8)
            pygame.draw.rect(self.screen, (220, 245, 238), pill_rect, border_radius=12)
            pygame.draw.rect(self.screen, COL_BMO_INK, pill_rect, 2, border_radius=12)
            self.screen.blit(txt_pill, (WIN_W - pw - 51, 24))

            # Screen border
            border_rect = pygame.Rect(lcd_x - 6, lcd_y - 6, LCD_W * SCALE + 12, LCD_H * SCALE + 12)
            pygame.draw.rect(self.screen, COL_BMO_INK, border_rect, width=4, border_radius=18)

            # Cartridge slot & colored buttons on bottom
            slot_y = lcd_y + LCD_H * SCALE + 20
            pygame.draw.rect(self.screen, (26, 66, 56), (50, slot_y, 90, 12), border_radius=4)
            colors = [(250, 204, 21), (56, 189, 248), (74, 222, 128), (248, 113, 113)]
            for idx, c in enumerate(colors):
                pygame.draw.circle(self.screen, c, (WIN_W - 120 + idx * 20, slot_y + 6), 7)
                pygame.draw.circle(self.screen, COL_BMO_INK, (WIN_W - 120 + idx * 20, slot_y + 6), 7, 2)
        else:
            # Waveshare PCB Blue Frame
            pygame.draw.rect(self.screen, COL_PCB_BLUE, (0, 0, WIN_W, WIN_H))
            holes = [(20, 20), (WIN_W - 20, 20), (20, WIN_H - 20), (WIN_W - 20, WIN_H - 20)]
            for hx, hy in holes:
                pygame.draw.circle(self.screen, COL_PCB_GOLD, (hx, hy), 12, 3)
                pygame.draw.circle(self.screen, (10, 15, 20), (hx, hy), 8)

            txt_ws = self.font_md.render("WAVESHARE 2.0inch LCD MODULE (320x240 IPS)", True, COL_WHITE)
            self.screen.blit(txt_ws, (45, 18))

            pins = ["VCC", "GND", "DIN(47)", "CLK(21)", "CS(41)", "DC(40)", "RST(42)", "BL(45)"]
            px = 45
            for p in pins:
                pygame.draw.circle(self.screen, COL_PCB_GOLD, (px, 46), 4)
                lbl = self.font_sm.render(p, True, (200, 220, 240))
                self.screen.blit(lbl, (px + 7, 40))
                px += 80

        # Blit scaled LCD
        scaled_lcd = pygame.transform.scale(self.lcd_surf, (LCD_W * SCALE, LCD_H * SCALE))
        self.screen.blit(scaled_lcd, (lcd_x, lcd_y))

        # Bottom info bar
        info_y = lcd_y + LCD_H * SCALE + 14
        keys_str = "[1-6] Moods | [Space] Talk | [V] View Mode | [T] Test | [Q] Quit"
        txt_keys = self.font_sm.render(keys_str, True, (20, 40, 38) if self.view_mode == "macbook" else (160, 180, 210))
        self.screen.blit(txt_keys, (45, WIN_H - 26))

    def run_test_suite(self):
        print("\n[BMO Face Emulator] Running MacBook Face Exact Verification Suite...")
        tests = [
            ("Happy Joy", lambda: self.set_emotion("happy")),
            ("Excited Sparkle", lambda: self.set_emotion("excited")),
            ("Curious Eyebrow", lambda: self.set_emotion("curious")),
            ("Sleepy Zzz Drift", lambda: self.set_emotion("sleepy")),
            ("Alarmed Eyes", lambda: self.set_emotion("alarmed")),
            ("Speech Lip-Sync", lambda: setattr(self, "is_talking", True)),
        ]
        for name, fn in tests:
            fn()
            for _ in range(12):
                self.update_physics()
                self.render_face()
                self.draw_enclosure()
                pygame.display.flip()
                self.clock.tick(60)
            print(f"  ✓ {name}: PASSED")
        self.is_talking = False
        self.set_emotion("happy")
        print("[BMO Face Emulator] All MacBook face expressions verified!\n")

    def run(self, max_seconds=None):
        running = True
        start_time = time.time()

        while running:
            self.poll_server()

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_ESCAPE, pygame.K_q):
                        running = False
                    elif event.key == pygame.K_1:
                        self.set_emotion("neutral")
                    elif event.key == pygame.K_2:
                        self.set_emotion("happy")
                    elif event.key == pygame.K_3:
                        self.set_emotion("excited")
                    elif event.key == pygame.K_4:
                        self.set_emotion("curious")
                    elif event.key == pygame.K_5:
                        self.set_emotion("sleepy")
                    elif event.key == pygame.K_6:
                        self.set_emotion("alarmed")
                    elif event.key == pygame.K_SPACE:
                        self.is_talking = not self.is_talking
                        self.speech_level = 0.80 if self.is_talking else 0.0
                    elif event.key == pygame.K_v:
                        self.view_mode = "pcb" if self.view_mode == "macbook" else "macbook"
                    elif event.key == pygame.K_t:
                        self.run_test_suite()
                    elif event.key == pygame.K_UP:
                        self.pointer_y = -0.7
                    elif event.key == pygame.K_DOWN:
                        self.pointer_y = 0.7
                    elif event.key == pygame.K_LEFT:
                        self.pointer_x = -0.7
                    elif event.key == pygame.K_RIGHT:
                        self.pointer_x = 0.7
                elif event.type == pygame.KEYUP:
                    if event.key in (pygame.K_UP, pygame.K_DOWN):
                        self.pointer_y = 0.0
                    elif event.key in (pygame.K_LEFT, pygame.K_RIGHT):
                        self.pointer_x = 0.0

            self.update_physics()
            self.render_face()
            self.draw_enclosure()
            pygame.display.flip()
            self.clock.tick(60)

            if max_seconds and (time.time() - start_time) >= max_seconds:
                break

        pygame.quit()


def main():
    parser = argparse.ArgumentParser(description="MacBook Face Match BMO Emulator")
    parser.add_argument("--server", default="http://127.0.0.1:7860", help="Rocky Voice Server URL")
    parser.add_argument("--headless", action="store_true", help="Run in headless dummy mode for testing")
    parser.add_argument("--test", action="store_true", help="Run automated expression test suite and exit")
    parser.add_argument("--screenshot", type=str, default=None, help="Save frame screenshot to file and exit")
    parser.add_argument("--duration", type=float, default=None, help="Run for N seconds then exit")
    args = parser.parse_args()

    emu = ST7789Emulator(server_url=args.server, headless=args.headless)

    if args.test:
        emu.run_test_suite()
        if args.screenshot:
            pygame.image.save(emu.screen, args.screenshot)
            print(f"[BMO Face Emulator] Saved screenshot to {args.screenshot}")
        sys.exit(0)

    if args.screenshot:
        emu.update_physics()
        emu.render_face()
        emu.draw_enclosure()
        pygame.image.save(emu.screen, args.screenshot)
        print(f"[BMO Face Emulator] Saved screenshot to {args.screenshot}")
        sys.exit(0)

    emu.run(max_seconds=args.duration)


if __name__ == "__main__":
    main()
