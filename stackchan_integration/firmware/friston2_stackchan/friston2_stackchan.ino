// Friston-3 x StackChan K151 / LCD MODULE E | camera + microphone + v4 audio
// Board: M5CoreS3 | USB CDC On Boot: Enabled | M5GFX >= 0.2.27
// Libraries: M5Unified, M5Stack-Avatar (Avatar.h)
// The v0.3 PING / INFO / FACE commands remain compatible.
// v0.4 additions:
//   VOLUME 0..160                -> OK VOLUME <number>
//   AUDIO <byte_count> <crc32>  -> READY AUDIO
//   Host sends 128 bytes maximum, waits for ACK <cumulative_byte_count>.
//   On final ACK:             -> AUDIO STARTED -> AUDIO DONE
//   AUDIO4 <sample_count> <crc32> -> READY AUDIO (4-bit IMA ADPCM, low nibble first)
//        Host sends ceil(sample_count/2) encoded bytes with ACK128.
//        Firmware decodes to 16-bit PCM in PSRAM before speaker playback.
//        Legacy AUDIO PCM16 remains supported for easy rollback.
//   AUDIO4B <sample_count> <crc32> uses ACK1024 and a 4096-byte USB RX buffer.
// Do not use this simultaneously with another COM10 client / Arduino Monitor.
// v0.5: CAMERA <320|640>, CAMERA CLOSE, MIC <TOUCH|VAD> <wait_ms>
// <max_ms> <silence_ms> <rms_threshold>, CANCEL. Device-to-host DATA packets
// use length + CRC32 + READ DATA / ACK1024. Camera and microphone use the
// existing native USB CDC connection; no Wi-Fi or Internet is required.
// v0.6: official StackChan-BSP servo motion commands (MOVE/HOME/STOP).
// Camera owns the internal I2C bus until CAMERA CLOSE; microphone/audio commands
// close it automatically before using M5 hardware.

#include <Arduino.h>
#include <Avatar.h>
#include <M5Unified.h>
#include <M5StackChan.h>
#include <esp_camera.h>
#include <esp_heap_caps.h>
#include <img_converters.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

using m5avatar::Avatar;
using m5avatar::Expression;

namespace {

constexpr size_t kLineMax = 80;
constexpr uint32_t kSampleRate = 16000;
constexpr size_t kMaxPcmBytes = 384000;   // <= 12.0 seconds (16k * 2 * 12)
constexpr uint32_t kRxTimeoutMs = 10000;
constexpr size_t kLegacyAckBytes = 128;
constexpr size_t kFastAckBytes = 1024;
size_t ackChunkBytes = kLegacyAckBytes;
bool fastAudioReady = false;
constexpr int kAdpcmStepTable[89] = {
     7,     8,     9,    10,    11,    12,    13,    14,    16,
    17,    19,    21,    23,    25,    28,    31,    34,    37,
    41,    45,    50,    55,    60,    66,    73,    80,    88,
    97,   107,   118,   130,   143,   157,   173,   190,   209,
   230,   253,   279,   307,   337,   371,   408,   449,   494,
   544,   598,   658,   724,   796,   876,   963,  1060,  1166,
  1282,  1411,  1552,  1707,  1878,  2066,  2272,  2499,  2749,
  3024,  3327,  3660,  4026,  4428,  4871,  5358,  5894,  6484,
  7132,  7845,  8630,  9493, 10442, 11487, 12635, 13899, 15289,
 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767
};
constexpr int8_t kAdpcmIndexDelta[16] = {
  -1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8
};
constexpr uint32_t kLipRefreshMs = 55;
char lineBuffer[kLineMax] = {};
size_t lineUsed = 0;
bool lineTooLong = false;

Avatar avatar;
bool speakerReady = false;
uint8_t speakerVolume = 80;
bool cameraReady = false;
bool cameraOwnsBus = false;
unsigned cameraWidth = 0;
uint32_t lastCameraAt = 0;
constexpr uint32_t kCameraIdleMs = 10000;
constexpr uint32_t kDataAckTimeoutMs = 3000;
constexpr uint32_t kDataTotalTimeoutMs = 20000;
constexpr size_t kMaxJpegBytes = 300000;

void animateMouth();
bool closeCamera();
bool ensureSpeaker();
struct ControlLine;
bool micCancelRequested(ControlLine& reader);

enum class FaceMode : uint8_t {
  Idle, Thinking, Speaking, Happy, Neutral, Doubt, Sad, Angry, Sleepy
};

struct FaceEntry { const char* key; FaceMode value; };
constexpr FaceEntry kFaces[] = {
    {"IDLE", FaceMode::Idle}, {"THINKING", FaceMode::Thinking},
    {"SPEAKING", FaceMode::Speaking}, {"HAPPY", FaceMode::Happy},
    {"NEUTRAL", FaceMode::Neutral}, {"DOUBT", FaceMode::Doubt},
    {"SAD", FaceMode::Sad}, {"ANGRY", FaceMode::Angry},
    {"SLEEPY", FaceMode::Sleepy},
};

FaceMode currentFace = FaceMode::Idle;
float smoothedMouth = 0.0f;
uint32_t lastLipAt = 0;
size_t lipStep = 0;

// The entire clip is stored in PSRAM during transfer and throughout playback.
// This avoids audio underruns caused by host serial jitter. Never free it while
// M5.Speaker is playing: the speaker keeps a pointer to the PCM samples.
uint8_t* pcmBuffer = nullptr;
size_t pcmExpected = 0;
size_t pcmReceived = 0;
size_t wireExpected = 0;
size_t wireReceived = 0;
bool adpcm4 = false;
size_t decodedSamples = 0;
int32_t adpcmPredictor = 0;
int adpcmIndex = 0;
uint32_t expectedCrc = 0;
uint32_t crcProgress = 0xFFFFFFFFU;
uint32_t lastRxAt = 0;
bool pcmReceiving = false;
bool pcmPlaying = false;
uint32_t playbackStartAt = 0;
uint32_t playbackDurationMs = 0;

void setFace(FaceMode next) {
  currentFace = next;
  smoothedMouth = 0.0f;
  lipStep = 0;
  avatar.setMouthOpenRatio(0.0f);
  switch (next) {
    case FaceMode::Idle:
    case FaceMode::Speaking:
    case FaceMode::Happy:
      avatar.setExpression(Expression::Happy);
      break;
    case FaceMode::Thinking:
    case FaceMode::Doubt:
      avatar.setExpression(Expression::Doubt);
      break;
    case FaceMode::Neutral:
      avatar.setExpression(Expression::Neutral);
      break;
    case FaceMode::Sad:
      avatar.setExpression(Expression::Sad);
      break;
    case FaceMode::Angry:
      avatar.setExpression(Expression::Angry);
      break;
    case FaceMode::Sleepy:
      avatar.setExpression(Expression::Sleepy);
      break;
  }
}

// Only the command-handling task touches the shared internal I2C bus. Camera
// setup uses esp32-camera's SCCB driver; microphone, touch, power and speaker
// use M5Unified. Do not run M5.update() while SCCB owns those pins.
bool closeCamera() {
  if (cameraReady) {
    esp_camera_deinit();
    cameraReady = false;
    cameraWidth = 0;
  }
  if (cameraOwnsBus) {
    if (!M5.In_I2C.begin()) return false;
    cameraOwnsBus = false;
  }
  return true;
}

bool ensureSpeaker() {
  if (!closeCamera()) return false;
  M5.Mic.end();
  if (!speakerReady) speakerReady = M5.Speaker.begin();
  if (speakerReady) M5.Speaker.setVolume(speakerVolume);
  return speakerReady;
}

void serviceUi() {
  // Motion has its own FreeRTOS update task. This updates M5 and touch only.
  if (!cameraOwnsBus) M5StackChan.update();
  animateMouth();
}

struct ControlLine {
  char text[kLineMax] = {};
  size_t used = 0;
  bool overflow = false;

  // 0=incomplete, 1=complete, -1=line too long.
  int put(char ch) {
    if (ch == '\r') return 0;
    if (ch == '\n') {
      text[used] = '\0';
      const int result = overflow ? -1 : 1;
      used = 0;
      overflow = false;
      return result;
    }
    if (used + 1 < sizeof(text)) text[used++] = ch;
    else overflow = true;
    return 0;
  }
};

uint32_t dataCrc32(const uint8_t* data, size_t count) {
  uint32_t crc = 0xFFFFFFFFU;
  for (size_t i = 0; i < count; ++i) {
    crc ^= data[i];
    for (uint8_t bit = 0; bit < 8; ++bit) {
      crc = (crc >> 1) ^ ((crc & 1U) ? 0xEDB88320U : 0U);
    }
    if ((i & 4095U) == 0) delay(0);
  }
  return ~crc;
}

// Returns 1=expected line, 0=timeout, -1=CANCEL, -2=unexpected line.
int waitDataControl(const char* expected, uint32_t transferStarted) {
  ControlLine reader;
  const uint32_t started = millis();
  while (millis() - started < kDataAckTimeoutMs &&
         millis() - transferStarted < kDataTotalTimeoutMs) {
    while (Serial.available() > 0) {
      const int ready = reader.put(static_cast<char>(Serial.read()));
      if (!ready) continue;
      if (ready < 0) return -2;
      if (strcmp(reader.text, "CANCEL") == 0) return -1;
      if (strcmp(reader.text, expected) == 0) return 1;
      if (reader.text[0] != '\0') return -2;
    }
    // A binary transfer is exclusive. In particular no diagnostic text or
    // unrelated command reply may be interleaved with its bytes.
    delay(1);
  }
  return 0;
}

bool sendData(const char* format, const uint8_t* data, size_t count,
              unsigned first, unsigned second) {
  const uint32_t crc = dataCrc32(data, count);
  Serial.printf("DATA %s %lu %08lx %u %u\n", format,
                static_cast<unsigned long>(count),
                static_cast<unsigned long>(crc), first, second);
  const uint32_t started = millis();
  int response = waitDataControl("READ DATA", started);
  size_t sent = 0;
  while (response == 1 && sent < count) {
    const size_t block = (count - sent < kFastAckBytes)
                             ? count - sent : kFastAckBytes;
    size_t written = 0;
    while (written < block) {
      if (millis() - started >= kDataTotalTimeoutMs) {
        response = 0;
        break;
      }
      const size_t n = Serial.write(data + sent + written, block - written);
      written += n;
      if (n == 0) delay(1);
    }
    if (response != 1) break;
    sent += block;
    char expected[40];
    snprintf(expected, sizeof(expected), "ACK %lu", static_cast<unsigned long>(sent));
    response = waitDataControl(expected, started);
  }
  if (response == 1 && sent == count) {
    Serial.println("DONE DATA");
    return true;
  }
  Serial.println(response == -1 ? "ERR DATA_CANCELLED" :
                 response == -2 ? "ERR DATA_ACK" : "ERR DATA_TIMEOUT");
  return false;
}

bool openCamera(unsigned width) {
  // RGB565 DMA has a fixed expected frame byte count. Reconfigure the entire
  // driver on a resolution change; set_framesize alone is insufficient.
  if ((cameraReady && cameraWidth != width) || (!cameraReady && cameraOwnsBus)) {
    if (!closeCamera()) {
      Serial.println("ERR I2C_RESTORE");
      return false;
    }
  }
  if (!cameraReady) {
    if (!psramFound()) {
      Serial.println("ERR CAMERA_PSRAM");
      return false;
    }
    M5.Mic.end();
    M5.Speaker.end();
    speakerReady = false;
    M5.In_I2C.release();
    cameraOwnsBus = true;
    // Official K151 / CoreS3 GC0308 pin map. GC0308 cannot emit JPEG itself.
    // https://docs.m5stack.com/en/arduino/stackchan/camera
    camera_config_t cfg = {};
    cfg.pin_pwdn = -1;
    cfg.pin_reset = -1;
    cfg.pin_xclk = -1;  // The board provides the sensor clock.
    cfg.pin_sccb_sda = 12;
    cfg.pin_sccb_scl = 11;
    cfg.pin_d0 = 39;
    cfg.pin_d1 = 40;
    cfg.pin_d2 = 41;
    cfg.pin_d3 = 42;
    cfg.pin_d4 = 15;
    cfg.pin_d5 = 16;
    cfg.pin_d6 = 48;
    cfg.pin_d7 = 47;
    cfg.pin_vsync = 46;
    cfg.pin_href = 38;
    cfg.pin_pclk = 45;
    cfg.xclk_freq_hz = 20000000;
    cfg.ledc_timer = LEDC_TIMER_0;
    cfg.ledc_channel = LEDC_CHANNEL_0;
    cfg.pixel_format = PIXFORMAT_RGB565;
    cfg.frame_size = width == 640 ? FRAMESIZE_VGA : FRAMESIZE_QVGA;
    cfg.jpeg_quality = 0;  // unused for RGB565; frame2jpg quality is below.
    cfg.fb_count = 1;
    cfg.fb_location = CAMERA_FB_IN_PSRAM;
    cfg.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
    cfg.sccb_i2c_port = -1;
    const esp_err_t err = esp_camera_init(&cfg);
    if (err != ESP_OK) {
      closeCamera();
      Serial.printf("ERR CAMERA_INIT 0x%lx\n", static_cast<unsigned long>(err));
      return false;
    }
    cameraReady = true;
    cameraWidth = width;
  }
  sensor_t* sensor = esp_camera_sensor_get();
  if (sensor == nullptr) {
    closeCamera();
    Serial.println("ERR CAMERA_SIZE");
    return false;
  }
  cameraWidth = width;
  lastCameraAt = millis();
  return true;
}

void captureCamera(unsigned width) {
  if (!openCamera(width)) return;
  // The single queued frame may predate this request (or a size switch).
  // Drop it and fetch a fresh frame instead of returning a stale scene.
  camera_fb_t* frame = esp_camera_fb_get();
  if (frame == nullptr) {
    closeCamera();
    Serial.println("ERR CAMERA_FRAME");
    return;
  }
  esp_camera_fb_return(frame);
  frame = esp_camera_fb_get();
  if (frame == nullptr) {
    closeCamera();
    Serial.println("ERR CAMERA_FRAME");
    return;
  }
  uint8_t* jpeg = nullptr;
  size_t jpegSize = 0;
  const unsigned actualWidth = static_cast<unsigned>(frame->width);
  const unsigned actualHeight = static_cast<unsigned>(frame->height);
  const bool ok = frame2jpg(frame, 75, &jpeg, &jpegSize);
  esp_camera_fb_return(frame);
  if (!ok || jpeg == nullptr || jpegSize == 0 || jpegSize > kMaxJpegBytes) {
    free(jpeg);
    closeCamera();
    Serial.println("ERR CAMERA_JPEG");
    return;
  }
  const bool sent = sendData("JPEG", jpeg, jpegSize, actualWidth, actualHeight);
  free(jpeg);
  lastCameraAt = millis();
  if (!sent) closeCamera();
}

constexpr size_t kMicFrameSamples = 320;  // 20 ms at 16 kHz.
constexpr size_t kMicPreRollSamples = 3200;  // 200 ms of speech onset.
constexpr size_t kMicStartupSamples = 3200;  // 200 ms to observe task startup.
// These small working buffers stay out of the Arduino task's stack. The full
// utterance is allocated in PSRAM and is never saved to flash or microSD.
int16_t micStartup[kMicStartupSamples];
int16_t micWorking[2][kMicFrameSamples];
int16_t* const micFrames[3] = {micStartup, micWorking[0], micWorking[1]};
int16_t micPreRoll[kMicPreRollSamples];

bool micCancelRequested(ControlLine& reader) {
  while (Serial.available() > 0) {
    const int ready = reader.put(static_cast<char>(Serial.read()));
    if (!ready) continue;
    if (ready > 0 && strcmp(reader.text, "CANCEL") == 0) return true;
    if (ready < 0 || reader.text[0] != '\0') Serial.println("ERR MIC_BUSY");
  }
  return false;
}

bool touchedNow() {
  return M5.Touch.getCount() > 0 && M5.Touch.getDetail(0).wasPressed();
}

uint32_t centeredRms(const int16_t* data, size_t count) {
  int64_t sum = 0;
  int64_t squareSum = 0;
  for (size_t i = 0; i < count; ++i) {
    const int32_t sample = data[i];
    sum += sample;
    squareSum += static_cast<int64_t>(sample) * sample;
  }
  const double mean = static_cast<double>(sum) / count;
  const double variance = static_cast<double>(squareSum) / count - mean * mean;
  return variance > 0.0 ? static_cast<uint32_t>(sqrt(variance)) : 0;
}

void captureMic(const char* args) {
  char mode[8] = {};
  char extra = 0;
  unsigned long waitMs = 0, maxMs = 0, silenceMs = 0, threshold = 0;
  if (sscanf(args, "%7s %lu %lu %lu %lu %c", mode, &waitMs, &maxMs,
             &silenceMs, &threshold, &extra) != 5 ||
      (strcmp(mode, "TOUCH") != 0 && strcmp(mode, "VAD") != 0) ||
      waitMs < 100 || waitMs > 10000 || maxMs < 1000 || maxMs > 12000 ||
      silenceMs < 200 || silenceMs > 2000 || threshold < 30 || threshold > 10000) {
    Serial.println("ERR MIC_HEADER");
    return;
  }
  if (!closeCamera()) {
    Serial.println("ERR I2C_RESTORE");
    return;
  }
  const bool touchMode = strcmp(mode, "TOUCH") == 0;
  ControlLine control;
  if (touchMode) {
    // Idle TOUCH polling does not allocate, sample the mic, or restart audio.
    // Only a physical display touch starts the capture hardware.
    const uint32_t waitingSince = millis();
    bool touched = false;
    while (millis() - waitingSince < waitMs) {
      M5.update();
      if (micCancelRequested(control)) {
        Serial.println("MIC CANCELLED");
        return;
      }
      if (touchedNow()) { touched = true; break; }
      delay(3);
    }
    if (!touched) {
      Serial.println("MIC EMPTY");
      return;
    }
  }
  const size_t maxSamples = (maxMs * kSampleRate) / 1000;
  int16_t* clip = static_cast<int16_t*>(
      heap_caps_malloc(maxSamples * sizeof(int16_t),
                       MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (clip == nullptr) {
    Serial.println("ERR MIC_PSRAM");
    return;
  }
  M5.Speaker.end();
  speakerReady = false;
  auto config = M5.Mic.config();
  config.sample_rate = kSampleRate;
  M5.Mic.config(config);
  if (!M5.Mic.begin()) {
    M5.Mic.end();
    heap_caps_free(clip);
    ensureSpeaker();
    Serial.println("ERR MIC_INIT");
    return;
  }

  bool recording = false;
  bool pendingTouch = touchMode;
  bool cancelled = false;
  bool failed = false;
  bool startupFrame = true;
  uint32_t consecutiveVoice = 0;
  uint32_t voicedFrames = 0;
  size_t preUsed = 0, preWrite = 0, used = 0, lastVoiceEnd = 0;
  const uint32_t began = millis();
  uint32_t recordBegan = 0;
  uint32_t lastVoiceAt = 0;
  setFace(FaceMode::Neutral);

  // record() queues an asynchronous capture. A third accepted request means
  // the oldest of these three alternating buffers has been released. Reading
  // a buffer immediately after record() would race DMA and yield stale audio.
  if (!M5.Mic.record(micFrames[0], kMicStartupSamples, kSampleRate, false)) failed = true;
  if (!failed) {
    // M5Unified 0.2.11 gates isRecording() on a task-start flag. Observe the
    // first request starting before filling its second slot; delay(1) alone
    // would not establish that the asynchronous capture task is running.
    // This initial request lasts 200 ms so normal task scheduling is unlikely
    // to hide its entire active interval. A missed interval fails closed.
    const uint32_t startup = millis();
    while (M5.Mic.isRecording() == 0) {
      M5.update();
      if (micCancelRequested(control)) { cancelled = true; break; }
      if (millis() - startup > 600) { failed = true; break; }
      delay(1);
    }
    if (!cancelled && !failed &&
        !M5.Mic.record(micFrames[1], kMicFrameSamples, kSampleRate, false)) {
      failed = true;
    }
  }
  size_t submit = 2, completed = 0;
  while (!failed && !cancelled) {
    M5.update();
    if (touchedNow()) {
      if (recording) break;
      if (touchMode) pendingTouch = true;
    }
    if (micCancelRequested(control)) { cancelled = true; break; }
    if (!recording && !pendingTouch && millis() - began >= waitMs) break;
    if (recording && millis() - recordBegan >= maxMs) break;

    // Leave the two-slot capture queue room before submitting. A stopped I2S
    // stream gets a finite error instead of an unbounded queue wait.
    const uint32_t queueWait = millis();
    delay(1);  // allow M5Unified's capture task to start after begin().
    while (M5.Mic.isRecording() >= 2) {
      M5.update();
      if (touchedNow()) {
        if (recording) { pendingTouch = true; }
        else if (touchMode) pendingTouch = true;
      }
      if (micCancelRequested(control)) { cancelled = true; break; }
      if (millis() - queueWait > 600) { failed = true; break; }
      delay(1);
    }
    if (cancelled || failed || (recording && pendingTouch)) break;
    if (!M5.Mic.record(micFrames[submit], kMicFrameSamples, kSampleRate, false)) {
      failed = true;
      break;
    }
    const int16_t* frame = micFrames[completed];
    submit = (submit + 1) % 3;
    completed = (completed + 1) % 3;
    const uint32_t now = millis();

    // Discard the first, 200 ms startup request to let the ADC settle. Later
    // requests use only 320 samples even when reusing that larger buffer.
    // This is an energy endpoint detector, not ESP-SR or noise cancellation.
    if (startupFrame) { startupFrame = false; continue; }
    const uint32_t rms = centeredRms(frame, kMicFrameSamples);
    const bool voice = rms >= threshold;
    consecutiveVoice = voice ? consecutiveVoice + 1 : 0;

    if (!recording) {
      for (size_t i = 0; i < kMicFrameSamples; ++i) {
        micPreRoll[preWrite] = frame[i];
        preWrite = (preWrite + 1) % kMicPreRollSamples;
        if (preUsed < kMicPreRollSamples) ++preUsed;
      }
      if (!(touchMode ? pendingTouch : consecutiveVoice >= 3)) continue;
      const size_t oldest = (preWrite + kMicPreRollSamples - preUsed) % kMicPreRollSamples;
      for (size_t i = 0; i < preUsed && used < maxSamples; ++i) {
        clip[used++] = micPreRoll[(oldest + i) % kMicPreRollSamples];
      }
      recording = true;
      pendingTouch = false;
      recordBegan = now;
      lastVoiceAt = now;
      // VAD mode needs three consecutive 20 ms frames to start. A single
      // click or clap is rejected; more voice is required before sending.
      voicedFrames = touchMode ? (voice ? 1 : 0) : consecutiveVoice;
      lastVoiceEnd = voice ? used : 0;
      setFace(FaceMode::Neutral);
      Serial.println("MIC STARTED");
      continue;
    }

    size_t take = kMicFrameSamples;
    if (take > maxSamples - used) take = maxSamples - used;
    memcpy(clip + used, frame, take * sizeof(int16_t));
    used += take;
    if (voice) {
      ++voicedFrames;
      lastVoiceAt = now;
      lastVoiceEnd = used;
    }
    if (used >= maxSamples) break;
    // Allow up to two seconds to start speaking after touching the screen.
    const uint32_t allowedSilence = voicedFrames ? silenceMs : 2000;
    if (now - lastVoiceAt >= allowedSilence) break;
  }

  // M5Unified reuses the I2S audio port. Fully stop capture before allowing
  // speaker playback or freeing a buffer still referenced by its task.
  M5.Mic.end();
  const bool restored = ensureSpeaker();
  setFace(FaceMode::Idle);
  if (cancelled) {
    Serial.println("MIC CANCELLED");
  } else if (failed) {
    Serial.println("ERR MIC_CAPTURE");
  } else if (!restored) {
    Serial.println("ERR SPEAKER_RESTORE");
  } else if (!recording || voicedFrames < 8 || lastVoiceEnd == 0) {
    Serial.println("MIC EMPTY");
  } else {
    // Keep 100 ms after the final voiced frame, rather than transferring the
    // entire endpoint-silence timeout and asking ASR to process it again.
    const size_t retained = lastVoiceEnd + kSampleRate / 10;
    if (used > retained) used = retained;
    sendData("PCM16", reinterpret_cast<const uint8_t*>(clip),
             used * sizeof(int16_t), kSampleRate, 1);
  }
  heap_caps_free(clip);
}

void clearPcm() {
  // Caller must stop playback or wait for completion before freeing the PCM.
  if (pcmBuffer != nullptr) {
    heap_caps_free(pcmBuffer);
    pcmBuffer = nullptr;
  }
  pcmExpected = 0;
  pcmReceived = 0;
  wireExpected = 0;
  wireReceived = 0;
  adpcm4 = false;
  ackChunkBytes = kLegacyAckBytes;
  decodedSamples = 0;
  adpcmPredictor = 0;
  adpcmIndex = 0;
  pcmReceiving = false;
  pcmPlaying = false;
  playbackDurationMs = 0;
}

void finishAudio() {
  // M5.Speaker.isPlaying() is checked by the caller before freeing memory.
  pcmPlaying = false;
  clearPcm();
  setFace(FaceMode::Idle);
  Serial.println("AUDIO DONE");
}

void failAudio(const char* reason) {
  // Called for failed receive or failed playRaw. Do not invoke during playback.
  const size_t received = wireReceived;
  const size_t expected = wireExpected;
  clearPcm();
  setFace(FaceMode::Idle);
  Serial.print("ERR ");
  Serial.print(reason);
  if (strcmp(reason, "AUDIO_RX_TIMEOUT") == 0) {
    Serial.print(" received=");
    Serial.print(static_cast<unsigned long>(received));
    Serial.print(" expected=");
    Serial.print(static_cast<unsigned long>(expected));
  }
  Serial.println();
}

// CRC-32 (IEEE, same as zlib.crc32 on Python), streaming update on receipt.
void crcUpdate(const uint8_t* data, size_t len) {
  for (size_t i = 0; i < len; ++i) {
    crcProgress ^= data[i];
    for (uint8_t b = 0; b < 8; ++b) {
      crcProgress = (crcProgress >> 1) ^ ((crcProgress & 1U) ? 0xEDB88320U : 0U);
    }
  }
}

void startPlayback() {
  pcmReceiving = false;
  if ((~crcProgress) != expectedCrc) {
    failAudio("AUDIO_CRC");
    return;
  }
  if (!speakerReady) {
    failAudio("SPEAKER_UNAVAILABLE");
    return;
  }

  const size_t sampleCount = pcmExpected / sizeof(int16_t);
  // M5Unified playRaw(ptr, SAMPLE_COUNT, rate, stereo, repeat, channel, stop_current).
  const bool ok = M5.Speaker.playRaw(
      reinterpret_cast<const int16_t*>(pcmBuffer),
      sampleCount, kSampleRate, false, 1, 0, true);
  if (!ok) {
    failAudio("SPEAKER_QUEUE");
    return;
  }
  setFace(FaceMode::Speaking);
  pcmPlaying = true;
  playbackStartAt = millis();
  playbackDurationMs = static_cast<uint32_t>((sampleCount * 1000ULL) / kSampleRate);
  lastLipAt = 0;
  Serial.println("AUDIO STARTED");
}

// Standard IMA ADPCM 4-bit nibble decoder. Reset state for each clip.
int16_t decodeAdpcmNibble(uint8_t code) {
  code &= 0x0F;
  const int step = kAdpcmStepTable[adpcmIndex];
  int delta = step >> 3;
  if (code & 1U) delta += step >> 2;
  if (code & 2U) delta += step >> 1;
  if (code & 4U) delta += step;
  adpcmPredictor += (code & 8U) ? -delta : delta;
  if (adpcmPredictor > 32767) adpcmPredictor = 32767;
  if (adpcmPredictor < -32768) adpcmPredictor = -32768;
  adpcmIndex += kAdpcmIndexDelta[code];
  if (adpcmIndex < 0) adpcmIndex = 0;
  if (adpcmIndex > 88) adpcmIndex = 88;
  return static_cast<int16_t>(adpcmPredictor);
}

void receivePcm() {
  if (!pcmReceiving) return;
  const int available = Serial.available();
  if (available <= 0) return;
  const size_t remaining = wireExpected - wireReceived;
  const size_t untilAck = ackChunkBytes - (wireReceived % ackChunkBytes);
  size_t toRead = static_cast<size_t>(available);
  if (toRead > remaining) toRead = remaining;
  if (toRead > untilAck) toRead = untilAck;
  if (toRead == 0) return;
  size_t n = 0;
  if (adpcm4) {
    uint8_t packet[kFastAckBytes];
    n = Serial.readBytes(reinterpret_cast<char*>(packet), toRead);
    if (n == 0) return;
    crcUpdate(packet, n);  // CRC checks WIRE bytes, not decoded audio.
    int16_t* samples = reinterpret_cast<int16_t*>(pcmBuffer);
    const size_t sampleTotal = pcmExpected / sizeof(int16_t);
    for (size_t i = 0; i < n; ++i) {
      if (decodedSamples < sampleTotal) {
        samples[decodedSamples++] = decodeAdpcmNibble(packet[i] & 0x0FU);
      }
      if (decodedSamples < sampleTotal) {
        samples[decodedSamples++] = decodeAdpcmNibble(packet[i] >> 4);
      }
    }
    pcmReceived = decodedSamples * sizeof(int16_t);
  } else {
    n = Serial.readBytes(reinterpret_cast<char*>(pcmBuffer + pcmReceived), toRead);
    if (n == 0) return;
    crcUpdate(pcmBuffer + pcmReceived, n);
    pcmReceived += n;
  }
  wireReceived += n;
  lastRxAt = millis();
  if ((wireReceived % ackChunkBytes) == 0 || wireReceived == wireExpected) {
    Serial.print("ACK ");
    Serial.println(static_cast<unsigned long>(wireReceived));
  }
  if (wireReceived == wireExpected) {
    if (pcmReceived != pcmExpected) failAudio("AUDIO_DECODE_SIZE");
    else startPlayback();
  }
}

void startReceive(const char* args, bool compressed, bool fast = false) {
  if (pcmReceiving || pcmPlaying) {
    Serial.println("ERR AUDIO_BUSY");
    return;
  }
  if (fast && !fastAudioReady) {
    Serial.println("ERR AUDIO_RX_BUFFER");
    return;
  }

  // AUDIO <pcm_bytes> <CRC32-of-PCM16> (legacy)
  // AUDIO4 <sample_count> <CRC32-of-ADPCM4> (4x smaller transport)
  char* sizeEnd = nullptr;
  const unsigned long count = strtoul(args, &sizeEnd, 10);
  if (sizeEnd == args || *sizeEnd != ' ') {
    Serial.println("ERR AUDIO_HEADER");
    return;
  }
  const char* crcText = sizeEnd + 1;
  if (strlen(crcText) != 8) {
    Serial.println("ERR AUDIO_HEADER");
    return;
  }
  for (size_t i = 0; i < 8; ++i) {
    const char ch = crcText[i];
    if (!((ch >= '0' && ch <= '9') || (ch >= 'A' && ch <= 'F') ||
          (ch >= 'a' && ch <= 'f'))) {
      Serial.println("ERR AUDIO_HEADER");
      return;
    }
  }
  const uint32_t crcValue = static_cast<uint32_t>(strtoul(crcText, nullptr, 16));
  if (count == 0 ||
      (compressed ? count > kMaxPcmBytes / sizeof(int16_t)
                  : count > kMaxPcmBytes || (count & 1U) != 0)) {
    Serial.println("ERR AUDIO_SIZE");
    return;
  }
  if (!ensureSpeaker()) {
    Serial.println("ERR SPEAKER_UNAVAILABLE");
    return;
  }

  const size_t pcmBytes = compressed ? static_cast<size_t>(count) * 2U
                                     : static_cast<size_t>(count);
  const size_t incomingBytes = compressed ? (static_cast<size_t>(count) + 1U) / 2U
                                          : static_cast<size_t>(count);
  pcmBuffer = static_cast<uint8_t*>(
      heap_caps_malloc(pcmBytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (pcmBuffer == nullptr) {
    Serial.println("ERR AUDIO_PSRAM");
    return;
  }
  pcmExpected = pcmBytes;
  pcmReceived = 0;
  wireExpected = incomingBytes;
  wireReceived = 0;
  adpcm4 = compressed;
  ackChunkBytes = fast ? kFastAckBytes : kLegacyAckBytes;
  decodedSamples = 0;
  adpcmPredictor = 0;
  adpcmIndex = 0;
  expectedCrc = crcValue;
  crcProgress = 0xFFFFFFFFU;
  pcmReceiving = true;
  lastRxAt = millis();
  Serial.println("READY AUDIO");
}

void handleLine(const char* line) {
  if (strcmp(line, "PING") == 0) {
    Serial.println("PONG");
    return;
  }
  if (strcmp(line, "INFO") == 0) {
    Serial.println(fastAudioReady
        ? "INFO FRISTON2_STACKCHAN_V4_AUDIO_PCM16_16K_ACK128_ADPCM4_ACK1024 CAMERA_JPEG_V1 MIC_PCM16_V1 SERVO_MOTION_V1 SERVO_TORQUE_FIX_V3"
        : "INFO FRISTON2_STACKCHAN_V4_AUDIO_PCM16_16K_ACK128_ADPCM4 CAMERA_JPEG_V1 MIC_PCM16_V1 SERVO_MOTION_V1 SERVO_TORQUE_FIX_V3");
    return;
  }
  if (strncmp(line, "AUDIO4B ", 8) == 0) {
    startReceive(line + 8, true, true);
    return;
  }
  if (strncmp(line, "AUDIO4 ", 7) == 0) {
    startReceive(line + 7, true);
    return;
  }
  if (strncmp(line, "AUDIO ", 6) == 0) {
    startReceive(line + 6, false);
    return;
  }
  if (pcmPlaying) {
    Serial.println("ERR AUDIO_BUSY");
    return;
  }
  if (strcmp(line, "CANCEL") == 0) {
    closeCamera();
    Serial.println("OK CANCEL");
    return;
  }
  if (strcmp(line, "HOME") == 0) {
    if (cameraOwnsBus && !closeCamera()) { Serial.println("ERR I2C_RESTORE"); return; }
    // BSP Servo::init() disables torque. Moving the animation target alone
    // does not enable it. Suppress auto-release during target/torque setup,
    // then restore the BSP's normal idle release behavior.
    M5StackChan.Motion.setAutoTorqueReleaseEnabled(false);
    M5StackChan.Motion.goHome(200);
    M5StackChan.Motion.setTorqueEnabled(true);
    M5StackChan.Motion.setAutoTorqueReleaseEnabled(true);
    Serial.println("OK HOME");
    return;
  }
  if (strcmp(line, "STOP") == 0) {
    if (cameraOwnsBus && !closeCamera()) { Serial.println("ERR I2C_RESTORE"); return; }
    M5StackChan.Motion.stop();
    Serial.println("OK STOP");
    return;
  }
  if (strncmp(line, "MOVE ", 5) == 0) {
    if (cameraOwnsBus && !closeCamera()) { Serial.println("ERR I2C_RESTORE"); return; }
    long x = 0, y = 0, speed = 300;
    char extra = 0;
    const int fields = sscanf(line + 5, "%ld %ld %ld %c", &x, &y, &speed, &extra);
    // StackChan BSP uses tenths of degrees. Keep Y away from mechanical stops.
    if (fields != 3 || x < -900 || x > 900 || y < 50 || y > 850 ||
        speed < 100 || speed > 800) {
      Serial.println("ERR MOVE_RANGE");
      return;
    }
    // Atomically set both targets through Motion's mutex. Do not energize
    // torque before setting the new targets, or a stale target could move us.
    M5StackChan.Motion.setAutoTorqueReleaseEnabled(false);
    M5StackChan.Motion.move(static_cast<int>(x), static_cast<int>(y),
                           static_cast<int>(speed));
    M5StackChan.Motion.setTorqueEnabled(true);
    M5StackChan.Motion.setAutoTorqueReleaseEnabled(true);
    Serial.printf("OK MOVE %ld %ld %ld\n", x, y, speed);
    return;
  }
  if (strcmp(line, "CAMERA CLOSE") == 0) {
    Serial.println(closeCamera() ? "OK CAMERA CLOSE" : "ERR I2C_RESTORE");
    return;
  }
  if (strncmp(line, "CAMERA ", 7) == 0) {
    if (strcmp(line + 7, "320") == 0) captureCamera(320);
    else if (strcmp(line + 7, "640") == 0) captureCamera(640);
    else Serial.println("ERR CAMERA_SIZE");
    return;
  }
  if (strncmp(line, "MIC ", 4) == 0) {
    captureMic(line + 4);
    return;
  }
  if (strncmp(line, "VOLUME ", 7) == 0) {
    char* end = nullptr;
    unsigned long level = strtoul(line + 7, &end, 10);
    if (end == line + 7 || *end != '\0' || level > 160) {
      Serial.println("ERR VOLUME_RANGE");
      return;
    }
    // Retain volume across Mic/Speaker I2S handovers. No codec/I2C access is
    // needed here while the camera owns the bus; AUDIO restores the speaker.
    speakerVolume = static_cast<uint8_t>(level);
    if (speakerReady && !cameraOwnsBus) M5.Speaker.setVolume(speakerVolume);
    Serial.print("OK VOLUME ");
    Serial.println(level);
    return;
  }
  if (strncmp(line, "FACE ", 5) == 0) {
    const char* requested = line + 5;
    for (const auto& entry : kFaces) {
      if (strcmp(requested, entry.key) == 0) {
        if (currentFace != entry.value) setFace(entry.value);
        Serial.print("OK FACE ");
        Serial.println(entry.key);
        return;
      }
    }
    Serial.println("ERR UNKNOWN_FACE");
    return;
  }
  Serial.println("ERR UNKNOWN_COMMAND");
}

void pollSerial() {
  while (Serial.available() > 0) {
    if (pcmReceiving) {
      receivePcm();
      continue;
    }
    const char ch = static_cast<char>(Serial.read());
    if (ch == '\r') continue;
    if (ch == '\n') {
      if (lineTooLong) {
        Serial.println("ERR LINE_TOO_LONG");
      } else if (lineUsed > 0) {
        lineBuffer[lineUsed] = '\0';
        handleLine(lineBuffer);
      }
      lineUsed = 0;
      lineTooLong = false;
      continue;
    }
    if (lineTooLong) continue;
    if (lineUsed + 1 < kLineMax) lineBuffer[lineUsed++] = ch;
    else lineTooLong = true;
  }
}

void animateMouth() {
  if (currentFace != FaceMode::Speaking) return;
  const uint32_t now = millis();
  if (now - lastLipAt < kLipRefreshMs) return;
  lastLipAt = now;

  if (pcmPlaying && pcmBuffer != nullptr) {
    // Envelope-driven lip motion: amplitude of current audio time slice.
    // It is approximate timing from millis(), NOT phoneme/viseme alignment.
    const size_t sampleCount = pcmExpected / sizeof(int16_t);
    size_t startSample = static_cast<size_t>((now - playbackStartAt) * 16ULL);
    if (startSample >= sampleCount) startSample = sampleCount - 1;
    const int16_t* samples = reinterpret_cast<const int16_t*>(pcmBuffer);
    const size_t windowEnd = (startSample + 640 < sampleCount)
                                 ? startSample + 640 : sampleCount;
    uint64_t sum = 0;
    for (size_t i = startSample; i < windowEnd; ++i) {
      const int32_t value = static_cast<int32_t>(samples[i]);
      sum += static_cast<uint32_t>(value < 0 ? -value : value);
    }
    const uint32_t avg = static_cast<uint32_t>(sum / (windowEnd - startSample));
    float target = static_cast<float>(avg) / 5500.0f;
    if (target > 0.86f) target = 0.86f;
    if (target < 0.10f) target = 0.02f;
    smoothedMouth = 0.38f * target + 0.62f * smoothedMouth;
    avatar.setMouthOpenRatio(smoothedMouth);
  } else {
    // Preserve v0.3's manual FACE SPEAKING animation.
    const float seq[] = {0.12f, 0.60f, 0.88f, 0.27f, 0.75f, 0.18f};
    avatar.setMouthOpenRatio(seq[lipStep]);
    lipStep = (lipStep + 1) % (sizeof(seq) / sizeof(seq[0]));
  }
}

void pollAudioState() {
  if (pcmReceiving && (millis() - lastRxAt) > kRxTimeoutMs) {
    failAudio("AUDIO_RX_TIMEOUT");
  }
  if (!pcmPlaying) return;
  const uint32_t elapsed = millis() - playbackStartAt;
  if (elapsed >= 250 && !M5.Speaker.isPlaying()) {
    finishAudio();
  } else if (elapsed > playbackDurationMs + 5000) {
    // A stopped speaker may release its DMA queue asynchronously. Wait for
    // isPlaying() to become false before freeing the source PCM buffer.
    M5.Speaker.stop();
    if (!M5.Speaker.isPlaying()) {
      pcmPlaying = false;
      failAudio("AUDIO_PLAY_TIMEOUT");
    }
  }
}

}  // namespace

void setup() {
  // StackChan.begin initializes the official servo driver and CoreS3.
  M5StackChan.begin();
  M5.Display.setRotation(1);
  M5.Display.fillScreen(TFT_BLACK);
  fastAudioReady = Serial.setRxBufferSize(4096) >= 4096;
  Serial.begin(115200);
  Serial.setTimeout(30);
  Serial.setTxTimeoutMs(200);
  Serial.setDebugOutput(false);
#if ARDUINO_USB_MODE && ARDUINO_USB_CDC_ON_BOOT
  if (Serial.setTxBufferSize(4096) < 4096) Serial.setTxBufferSize(256);
#endif
  speakerReady = M5.Speaker.begin();
  if (speakerReady) M5.Speaker.setVolume(speakerVolume);
  avatar.init(1);
  avatar.setIsAutoBlink(true);
  avatar.setSpeechText("");
  setFace(FaceMode::Idle);
  // Do not move on boot or serial reconnect. Motion starts only on HOME/MOVE.
  // BSP initializes torque OFF; command handlers explicitly enable it.
  M5StackChan.Motion.setAutoTorqueReleaseEnabled(true);
  Serial.println("READY FRISTON2_STACKCHAN_AUDIO");
  if (!speakerReady) Serial.println("WARN SPEAKER_INIT_FAILED");
}

void loop() {
  serviceUi();
  pollSerial();
  pollAudioState();
  animateMouth();
  if (cameraReady && millis() - lastCameraAt > kCameraIdleMs) closeCamera();
  delay(3);
}
