// swarm_oled.h: SSD1306 128x64 status screen (0x3C) - visible board state for the judges (doc 02 §8 item 2).
// Shows the same state as the servo: ACTIVE / ROGUE / REVOKED in big letters + height, QC count, reason.
// Board rules kept: shares the bus opened by nr.begin() -> begin(..., periphBegin=false) never calls Wire.begin();
// clock pinned to 100 kHz during and after transfers (400 kHz reads were corrupted on this board).
// A full refresh is ~1 KB over I2C (~100 ms at 100 kHz), so we redraw only on change, at most every 2 s otherwise.
#pragma once
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

#define OLED_ADDR          0x3C
#define OLED_W             128
#define OLED_H             64
#define OLED_MIN_REDRAW_MS 2000

class SsOled {
 public:
  SsOled() : d_(OLED_W, OLED_H, &Wire, -1, 100000UL, 100000UL) {}

  bool begin() {
    ok_ = d_.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR, false, false);   // reset=false (no pin), periphBegin=false
    if (ok_) { d_.clearDisplay(); d_.display(); }
    return ok_;
  }
  bool ok() const { return ok_; }

  // Bootscreen while the self-test runs.
  void boot(const char* name) {
    if (!ok_) return;
    d_.clearDisplay();
    d_.setTextColor(SSD1306_WHITE);
    d_.setTextSize(2); d_.setCursor(0, 0);  d_.print("SWARM");
    d_.setCursor(0, 18); d_.print("SHIELD");
    d_.setTextSize(1); d_.setCursor(0, 42); d_.print(name);
    d_.setCursor(0, 54); d_.print("self-test...");
    d_.display();
  }

  // state: 0 ACTIVE, 1 ROGUE, 2 REVOKED. force = redraw now (state changes).
  void status(int state, const char* reason, uint32_t height, uint32_t inQc, uint32_t commits, bool wifiUp,
              bool cryptoOk, uint32_t now, bool force) {
    if (!ok_) return;
    (void)reason; (void)inQc; (void)commits; (void)cryptoOk;     // kept for serial; the screen shows big text only
    bool stateChanged = state != lastState_ || wifiUp != lastWifi_;
    bool changed = stateChanged || height != lastHeight_;
    if (!force && !stateChanged && (!changed || now - lastDraw_ < OLED_MIN_REDRAW_MS)) return;
    lastState_ = state; lastHeight_ = height; lastWifi_ = wifiUp; lastDraw_ = now;

    // Readable from a distance: one big word (size 3, 18x24 px chars) + the height (size 2).
    const char* word = !wifiUp && state == 0 ? "NO WIFI" : state == 0 ? "ACTIVE" : state == 1 ? "ROGUE" : "REVOKED";
    d_.clearDisplay();
    if (state != 0) {                                            // ROGUE / REVOKED: inverted full screen
      d_.fillRect(0, 0, OLED_W, OLED_H, SSD1306_WHITE);
      d_.setTextColor(SSD1306_BLACK);
    } else {
      d_.setTextColor(SSD1306_WHITE);
    }
    d_.setTextSize(3);
    int16_t x1, y1; uint16_t w, h;
    d_.getTextBounds(word, 0, 0, &x1, &y1, &w, &h);
    d_.setCursor((OLED_W - (int)w) / 2, 6);
    d_.print(word);
    char num[16];
    snprintf(num, sizeof(num), "h %lu", (unsigned long)height);
    d_.setTextSize(2);
    d_.getTextBounds(num, 0, 0, &x1, &y1, &w, &h);
    d_.setCursor((OLED_W - (int)w) / 2, 42);
    d_.print(num);
    d_.display();
  }

 private:
  Adafruit_SSD1306 d_;
  bool ok_ = false;
  int lastState_ = -1;
  uint32_t lastHeight_ = 0, lastDraw_ = 0;
  bool lastWifi_ = false;
};
