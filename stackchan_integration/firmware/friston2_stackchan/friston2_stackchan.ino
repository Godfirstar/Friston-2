// Friston-3 x StackChan K151 / LCD MODULE E | camera + microphone + v4 audio
// Board: M5CoreS3 | USB CDC On Boot: Enabled | M5GFX >= 0.2.27
// Libraries: M5Unified, M5GFX, StackChan-BSP (M5StackChan.h).
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
// v0.7: large block faces. FACE mood survives audio playback; the mouth is
// an audio overlay. LISTENING / PROUD / SURPRISED extend the existing names.
// GESTURE NOD|SHAKE|TILT|LOOK_LEFT|LOOK_RIGHT|NONE starts a bounded, nonblocking
// shoulder gesture. Neutral is (0,450), in tenths of a degree. No boot motion.
// v1.1: password-protected local Wi-Fi AP, TCP 192.168.4.1:8765. The same
// line/binary protocol works over USB or TCP, with one command owner at a time.
// v1.2: GESTURE <TOKEN> [SHOULDER|DESK] [NOW|SPEECH] adds expressive
// two-axis finite presets. SPEECH begins with actual playback, not upload.
// ACK stays OK GESTURE <TOKEN>; NONE/capture/disconnect clear queued motion.
// No Internet, external router, cloud service, or association-triggered motion.

#include <Arduino.h>
#include <M5GFX.h>
#include <M5Unified.h>
#include <M5StackChan.h>
#include <WiFi.h>
#include <esp_arduino_version.h>
#include <lwip/sockets.h>
#include <errno.h>
#include "friston3_wifi_config.h"
#include <esp_camera.h>
#include <esp_heap_caps.h>
#include <img_converters.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

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

WiFiServer commandServer(FRISTON_WIFI_PORT);
WiFiClient commandClient;
bool wifiReady = false;
bool tcpOwner = false;
bool transportLost = false;
bool commandActive = false;
uint32_t lastLinkCheckAt = 0;
uint32_t lastUsbActivityAt = 0;
constexpr uint32_t kNetworkWriteTimeoutMs = 1500;
constexpr uint32_t kUsbClaimQuietMs = 1200;

void markTransportLost();
bool checkTransportLink(bool force = false);
void serviceTransport(bool allowClaim);
void reclaimTransport();

// Print/Stream preserve the protocol's exact bytes. No debug text is sent
// through this wrapper; only the current owner's responses enter the stream.
class ProtocolStream : public Stream {
 public:
  int available() override {
    if (!checkTransportLink()) return 0;
    return tcpOwner ? commandClient.available() : Serial.available();
  }

  int read() override {
    if (!checkTransportLink()) return -1;
    const int value = tcpOwner ? commandClient.read() : Serial.read();
    if (!tcpOwner && value >= 0) lastUsbActivityAt = millis();
    return value;
  }

  int peek() override {
    if (!checkTransportLink()) return -1;
    return tcpOwner ? commandClient.peek() : Serial.peek();
  }

  void flush() override {
    // TCP sends below already enqueue all bytes or invalidate the session.
    // WiFiClient.flush() has changed RX/TX semantics between Arduino versions.
    if (!tcpOwner && !transportLost) Serial.flush();
  }

  size_t readBytes(char* buffer, size_t length) {
    if (!checkTransportLink()) return 0;
    if (!tcpOwner) {
      const size_t n = Serial.readBytes(buffer, length);
      if (n != 0) lastUsbActivityAt = millis();
      return n;
    }
    size_t total = 0;
    const uint32_t started = millis();
    while (total < length && millis() - started < getTimeout()) {
      if (!checkTransportLink()) break;
      const int ready = commandClient.available();
      if (ready > 0) {
        const size_t count = (length - total < static_cast<size_t>(ready))
            ? length - total : static_cast<size_t>(ready);
        const int n = commandClient.read(reinterpret_cast<uint8_t*>(buffer + total), count);
        if (n > 0) total += static_cast<size_t>(n);
        else delay(1);
      } else {
        delay(1);
      }
    }
    return total;
  }

  size_t write(uint8_t value) override { return write(&value, 1); }

  size_t write(const uint8_t* data, size_t count) override {
    if (!checkTransportLink()) return 0;
    if (!tcpOwner) return Serial.write(data, count);
    size_t sent = 0;
    const uint32_t started = millis();
    while (sent < count) {
      if (!checkTransportLink()) break;
      // Nonblocking BSD send avoids WiFiClient's version-dependent long retry
      // loop. ACKs and each <=1024-byte block are completed or the link closes.
      const int n = ::send(commandClient.fd(), data + sent, count - sent, MSG_DONTWAIT);
      if (n > 0) {
        sent += static_cast<size_t>(n);
        continue;
      }
      if (n == 0 || (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR) ||
          millis() - started >= kNetworkWriteTimeoutMs) {
        markTransportLost();
        break;
      }
      delay(1);
    }
    return sent;
  }

  using Print::write;
};

ProtocolStream protocol;

M5Canvas faceCanvas(&M5.Display);
bool faceCanvasReady = false;
bool faceDirty = true;
uint32_t lastFaceAt = 0;
uint32_t nextBlinkAt = 3500;
uint32_t blinkUntil = 0;
bool wasBlinking = false;
bool speakerReady = false;
uint8_t speakerVolume = 80;
bool cameraReady = false;
bool cameraOwnsBus = false;
unsigned cameraWidth = 0;
uint32_t lastCameraAt = 0;
uint32_t lastCameraRestoreAttemptAt = 0;
constexpr uint32_t kCameraIdleMs = 10000;
constexpr uint32_t kCameraRestoreRetryMs = 1000;
constexpr uint32_t kDataAckTimeoutMs = 3000;
constexpr uint32_t kDataTotalTimeoutMs = 20000;
constexpr size_t kMaxJpegBytes = 300000;

void animateMouth();
void drawBlockFace();
void tickGesture();
void cancelGesture();
void clearPcm();
bool closeCamera();
bool ensureSpeaker();
struct ControlLine;
bool micCancelRequested(ControlLine& reader);

enum class FaceMode : uint8_t {
  Idle, Thinking, Speaking, Happy, Neutral, Doubt, Sad, Angry, Sleepy,
  Listening, Proud, Surprised
};

struct FaceEntry { const char* key; FaceMode value; };
constexpr FaceEntry kFaces[] = {
    {"IDLE", FaceMode::Idle}, {"THINKING", FaceMode::Thinking},
    {"SPEAKING", FaceMode::Speaking}, {"HAPPY", FaceMode::Happy},
    {"NEUTRAL", FaceMode::Neutral}, {"DOUBT", FaceMode::Doubt},
    {"SAD", FaceMode::Sad}, {"ANGRY", FaceMode::Angry},
    {"SLEEPY", FaceMode::Sleepy},
    {"LISTENING", FaceMode::Listening}, {"PROUD", FaceMode::Proud},
    {"SURPRISED", FaceMode::Surprised},
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

// GESTURE_V2: offsets around the exhibit's neutral pose (0,450).
// The BSP maps speed 0..1000 to critically damped spring parameters; it is
// NOT degrees/second. Holds leave time for a visible pose, with gentler returns.
// Pitch grows upward. There is no roll axis: TILT combines yaw and pitch.
struct GestureFrame { int16_t dx; int16_t dy; uint16_t holdMs; uint16_t speed; };
constexpr GestureFrame kNod[] = {{0,-60,600,280},{0,15,450,270},{0,0,750,220}};
constexpr GestureFrame kAgree[] = {{0,-40,400,350},{0,20,400,340},{0,-45,450,350},{0,0,800,230}};
constexpr GestureFrame kShake[] = {{-90,0,650,290},{90,0,750,310},{0,0,850,230}};
constexpr GestureFrame kTilt[] = {{60,-30,950,230},{0,0,800,210}};
constexpr GestureFrame kGreet[] = {{-30,15,450,270},{30,-55,650,280},{0,0,800,230}};
constexpr GestureFrame kThink[] = {{60,35,1100,230},{25,10,450,230},{0,0,750,210}};
constexpr GestureFrame kProud[] = {{0,70,900,270},{-30,40,550,250},{0,0,800,220}};
constexpr GestureFrame kChuckle[] = {{-20,-20,380,350},{20,15,380,350},{-15,-25,400,340},{0,0,800,230}};
constexpr GestureFrame kSurprised[] = {{0,80,550,370},{0,30,600,280},{0,0,800,220}};
constexpr GestureFrame kConcerned[] = {{-35,-40,1000,230},{0,-20,450,230},{0,0,800,210}};
constexpr GestureFrame kBow[] = {{0,-80,950,250},{0,-45,500,240},{0,0,850,220}};
constexpr GestureFrame kLookLeft[] = {{-120,0,1050,260},{0,0,900,230}};
constexpr GestureFrame kLookRight[] = {{120,0,1050,260},{0,0,900,230}};
constexpr GestureFrame kScan[] = {{-100,15,750,260},{100,15,1000,280},{0,0,950,230}};
constexpr GestureFrame kAttend[] = {{0,30,850,230},{0,0,750,210}};
constexpr GestureFrame kSettle[] = {{0,0,950,220}};
constexpr GestureFrame kBeat[] = {{0,-25,450,310},{0,0,700,230}};

struct GesturePreset { const char* name; const GestureFrame* frames; size_t count; };
#define GESTURE_PRESET(name, frames) {name, frames, sizeof(frames) / sizeof(frames[0])}
constexpr GesturePreset kGestures[] = {
  GESTURE_PRESET("NOD", kNod), GESTURE_PRESET("AGREE", kAgree),
  GESTURE_PRESET("SHAKE", kShake), GESTURE_PRESET("TILT", kTilt),
  GESTURE_PRESET("GREET", kGreet), GESTURE_PRESET("THINK", kThink),
  GESTURE_PRESET("PROUD", kProud), GESTURE_PRESET("CHUCKLE", kChuckle),
  GESTURE_PRESET("SURPRISED", kSurprised), GESTURE_PRESET("CONCERNED", kConcerned),
  GESTURE_PRESET("BOW", kBow), GESTURE_PRESET("LOOK_LEFT", kLookLeft),
  GESTURE_PRESET("LOOK_RIGHT", kLookRight), GESTURE_PRESET("SCAN", kScan),
  GESTURE_PRESET("ATTEND", kAttend), GESTURE_PRESET("SETTLE", kSettle),
  GESTURE_PRESET("BEAT", kBeat),
};
#undef GESTURE_PRESET

enum class GestureProfile : uint8_t { Shoulder, Desk };
const GestureFrame* gestureFrames = nullptr;
size_t gestureCount = 0;
size_t gestureIndex = 0;
uint32_t nextGestureAt = 0;
GestureProfile gestureProfile = GestureProfile::Shoulder;
const GesturePreset* pendingSpeechGesture = nullptr;
GestureProfile pendingSpeechProfile = GestureProfile::Shoulder;
uint32_t pendingSpeechExpiresAt = 0;
constexpr uint32_t kSpeechGestureTimeoutMs = 10000;

void cancelGesture() {
  const bool movingSequence = gestureFrames != nullptr;
  gestureFrames = nullptr;
  gestureCount = gestureIndex = 0;
  pendingSpeechGesture = nullptr;
  pendingSpeechExpiresAt = 0;
  // Cancellation holds the current pose; it never creates a return-to-center
  // movement near a shoulder or while a sensor is beginning capture.
  // A final frame may still be settling in the BSP after its scheduler hold
  // ends. Capture/cancel must stop that physical tail as well as queued frames.
  if (movingSequence || M5StackChan.Motion.isMoving()) M5StackChan.Motion.stop();
}

int gestureCoordinate(int offset, int limit) {
  if (gestureProfile == GestureProfile::Desk) offset = offset * 3 / 2;
  return offset < -limit ? -limit : (offset > limit ? limit : offset);
}

void tickGesture() {
  const uint32_t now = millis();
  if (pendingSpeechGesture != nullptr &&
      static_cast<int32_t>(now - pendingSpeechExpiresAt) >= 0) {
    pendingSpeechGesture = nullptr;
    pendingSpeechExpiresAt = 0;
  }
  if (gestureFrames == nullptr) return;
  if (cameraOwnsBus) { cancelGesture(); return; }
  if (static_cast<int32_t>(now - nextGestureAt) < 0) return;
  if (gestureIndex >= gestureCount) {
    gestureFrames = nullptr;
    gestureCount = gestureIndex = 0;
    return;
  }
  const GestureFrame& step = gestureFrames[gestureIndex++];
  const bool desk = gestureProfile == GestureProfile::Desk;
  const int x = gestureCoordinate(step.dx, desk ? 180 : 120);
  const int y = 450 + gestureCoordinate(step.dy, desk ? 120 : 80);
  const int speed = step.speed + (desk ? 40 : 0);
  // Preserve the v3 torque fix: establish a fresh target before enabling.
  M5StackChan.Motion.setAutoTorqueReleaseEnabled(false);
  M5StackChan.Motion.move(x, y, speed);
  M5StackChan.Motion.setTorqueEnabled(true);
  M5StackChan.Motion.setAutoTorqueReleaseEnabled(true);
  nextGestureAt = now + step.holdMs;
}

void beginGesture(const GesturePreset& preset, GestureProfile profile) {
  gestureFrames = preset.frames;
  gestureCount = preset.count;
  gestureIndex = 0;
  gestureProfile = profile;
  nextGestureAt = millis();
  tickGesture();
}

void startSpeechGesture() {
  if (pendingSpeechGesture == nullptr) return;
  const GesturePreset* preset = pendingSpeechGesture;
  const GestureProfile profile = pendingSpeechProfile;
  const bool expired = static_cast<int32_t>(millis() - pendingSpeechExpiresAt) >= 0;
  cancelGesture();
  if (!expired && !cameraOwnsBus) beginGesture(*preset, profile);
  // No unsolicited protocol lines: AUDIO STARTED/DONE retain their old framing.
}

void startGesture(const char* args) {
  char name[16] = {}, profileText[16] = {}, timingText[16] = {}, extra = 0;
  const int fields = sscanf(args, "%15s %15s %15s %c", name, profileText, timingText, &extra);
  if (fields < 1 || fields > 3) { protocol.println("ERR GESTURE_HEADER"); return; }
  GestureProfile profile = GestureProfile::Shoulder;
  bool onSpeech = false;
  if (fields >= 2) {
    if (strcmp(profileText, "DESK") == 0) profile = GestureProfile::Desk;
    else if (strcmp(profileText, "SHOULDER") == 0) {}
    else if (fields == 2 && strcmp(profileText, "NOW") == 0) {}
    else if (fields == 2 && strcmp(profileText, "SPEECH") == 0) onSpeech = true;
    else { protocol.println("ERR GESTURE_PROFILE"); return; }
  }
  if (fields == 3) {
    if (strcmp(timingText, "SPEECH") == 0) onSpeech = true;
    else if (strcmp(timingText, "NOW") != 0) { protocol.println("ERR GESTURE_TIMING"); return; }
  }
  const bool none = strcmp(name, "NONE") == 0;
  const GesturePreset* preset = nullptr;
  for (const auto& entry : kGestures) {
    if (strcmp(name, entry.name) == 0) { preset = &entry; break; }
  }
  if (!none && preset == nullptr) { protocol.println("ERR UNKNOWN_GESTURE"); return; }
  if (!none && cameraOwnsBus) { protocol.println("ERR GESTURE_CAMERA_BUSY"); return; }
  // Validate every field before touching the current action or pending speech.
  cancelGesture();
  if (!none) {
    if (onSpeech) {
      pendingSpeechGesture = preset;
      pendingSpeechProfile = profile;
      pendingSpeechExpiresAt = millis() + kSpeechGestureTimeoutMs;
    } else {
      beginGesture(*preset, profile);
    }
  }
  protocol.print("OK GESTURE ");
  protocol.println(name);
}

void markTransportLost() {
  if (!tcpOwner || transportLost) return;
  transportLost = true;
  commandClient.stop();  // Discard every buffered byte from the old session.
  cancelGesture();
  // BSP Motion.stop reads servo positions over UART 6/7, not camera SCCB 12/11.
  M5StackChan.Motion.stop();
  // Speaker owns pcmBuffer until its asynchronous queue has stopped. Leave
  // allocation cleanup to reclaimTransport(), outside the current command.
  if (pcmPlaying) M5.Speaker.stop();
  lineUsed = 0;
  lineTooLong = false;
}

bool checkTransportLink(bool force) {
  if (transportLost) return false;
  if (!tcpOwner) return true;
  const uint32_t now = millis();
  if (!force && now - lastLinkCheckAt < 30) return true;
  lastLinkCheckAt = now;
  if (WiFi.softAPgetStationNum() == 0 || !commandClient.connected()) {
    markTransportLost();
    return false;
  }
  // A half-closed peer is a lost session for this duplex protocol. Do not
  // execute commands cached by WiFiClient after the socket has reached EOF.
  uint8_t probe = 0;
  const int result = ::recv(commandClient.fd(), &probe, 1, MSG_PEEK | MSG_DONTWAIT);
  if (result == 0 || (result < 0 && errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR)) {
    markTransportLost();
    return false;
  }
  return true;
}

void reclaimTransport() {
  if (!transportLost || commandActive) return;
  if (pcmPlaying || M5.Speaker.isPlaying()) {
    M5.Speaker.stop();
    if (M5.Speaker.isPlaying()) return;
  }
  pcmPlaying = false;
  clearPcm();
  if (!closeCamera()) {
    // Leave cameraOwnsBus set: no new TCP owner or M5 I2C operation can start.
    // The loop retries restoration; USB can still issue CAMERA CLOSE/reset.
    Serial.println("WARN I2C_RESTORE_RETRY USB_AVAILABLE");
  }
  if (!cameraOwnsBus) M5.Mic.end();
  smoothedMouth = 0.0f;
  faceDirty = true;
  lineUsed = 0;
  lineTooLong = false;
  memset(lineBuffer, 0, sizeof(lineBuffer));
  // USB bytes received while TCP held ownership are never replayed afterward.
  while (Serial.available() > 0) Serial.read();
  tcpOwner = false;
  transportLost = false;
  // No new USB command has arrived, so a fresh TCP handshake need not wait
  // for the USB quiet window again after the old operation has fully stopped.
  lastUsbActivityAt = millis() - kUsbClaimQuietMs;
}

void serviceTransport(bool allowClaim) {
  checkTransportLink(true);
  if (transportLost) {
    reclaimTransport();
    return;
  }
  if (tcpOwner) {
    // USB remains physically available for flashing, but cannot inject bytes
    // into the TCP owner's line or binary transaction.
    while (Serial.available() > 0) Serial.read();
  }
  if (!wifiReady) return;
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  WiFiClient incoming = commandServer.accept();
#else
  WiFiClient incoming = commandServer.available();
#endif
  if (!incoming) return;
  const bool usbQuiet = millis() - lastUsbActivityAt >= kUsbClaimQuietMs;
  const bool busy = !allowClaim || tcpOwner || commandActive || pcmReceiving ||
                    pcmPlaying || cameraOwnsBus || gestureFrames != nullptr ||
                    pendingSpeechGesture != nullptr ||
                    lineUsed != 0 || lineTooLong || Serial.available() != 0 || !usbQuiet;
  if (busy) {
    static const char response[] = "ERR TRANSPORT_BUSY\n";
    ::send(incoming.fd(), response, sizeof(response) - 1, MSG_DONTWAIT);
    incoming.stop();
    return;
  }
  while (Serial.available() > 0) Serial.read();
  lineUsed = 0;
  lineTooLong = false;
  memset(lineBuffer, 0, sizeof(lineBuffer));
  // A previous USB MOVE may still be interpolating after its short reply.
  // Ownership change cancels that target; it never creates a new pose target.
  cancelGesture();
  M5StackChan.Motion.stop();
  commandClient = incoming;
  commandClient.setNoDelay(true);
  // Detect an unreachable host/link without a permanent stale owner. A hung
  // userspace process whose OS still ACKs is not detectable by TCP keepalive.
  // Normal idle dialogue has a live TCP stack; keepalives never move servos.
  const int one = 1, idleSeconds = 3, intervalSeconds = 1, probeCount = 3;
  setsockopt(commandClient.fd(), SOL_SOCKET, SO_KEEPALIVE, &one, sizeof(one));
#ifdef TCP_KEEPIDLE
  setsockopt(commandClient.fd(), IPPROTO_TCP, TCP_KEEPIDLE, &idleSeconds, sizeof(idleSeconds));
  setsockopt(commandClient.fd(), IPPROTO_TCP, TCP_KEEPINTVL, &intervalSeconds, sizeof(intervalSeconds));
  setsockopt(commandClient.fd(), IPPROTO_TCP, TCP_KEEPCNT, &probeCount, sizeof(probeCount));
#endif
  tcpOwner = true;
  lastLinkCheckAt = 0;
  protocol.println("READY FRISTON3_STACKCHAN_TCP");
}

void startWireless() {
#if FRISTON_WIFI_ENABLED
  const size_t passwordLength = strlen(FRISTON_WIFI_PASSWORD);
  if (passwordLength < 8 || passwordLength > 63 || strlen(FRISTON_WIFI_SSID) == 0 ||
      strlen(FRISTON_WIFI_SSID) > 32) {
    Serial.println("WARN WIFI_CONFIG_INVALID USB_AVAILABLE");
    return;
  }
  const IPAddress address(192, 168, 4, 1), netmask(255, 255, 255, 0);
  WiFi.persistent(false);
  if (!WiFi.mode(WIFI_AP) || !WiFi.softAPConfig(address, address, netmask) ||
      !WiFi.softAP(FRISTON_WIFI_SSID, FRISTON_WIFI_PASSWORD,
                   FRISTON_WIFI_CHANNEL, 0, 1)) {
    WiFi.mode(WIFI_OFF);
    Serial.println("WARN WIFI_AP_FAILED USB_AVAILABLE");
    return;
  }
  WiFi.setSleep(false);
  commandServer.begin();
  commandServer.setNoDelay(true);
  wifiReady = true;
  // USB-only startup diagnostic, not interleaved with binary responses.
  Serial.printf("READY WIFI_AP %s 192.168.4.1 %u\n", FRISTON_WIFI_SSID,
                static_cast<unsigned>(FRISTON_WIFI_PORT));
#endif
}

void setFace(FaceMode next) {
  currentFace = next;
  smoothedMouth = 0.0f;
  lipStep = 0;
  faceDirty = true;
  // Paint once immediately, including before a blocking microphone capture.
  drawBlockFace();
}

// A face is rendered by the command task only. The former Avatar draw task
// must not run alongside this renderer and race for the display. An 8-bit
// offscreen canvas uses about 75 KiB at 320x240 and avoids visible clearing.
// If allocation fails, direct drawing keeps the device usable.
void drawBlockFace() {
  if (pcmReceiving) return;  // Preserve tight ACK timing during audio upload.
  const uint32_t now = millis();
  if (!faceDirty && now - lastFaceAt < kLipRefreshMs) return;
  lastFaceAt = now;

  const bool blinkAllowed = currentFace != FaceMode::Sleepy &&
                            currentFace != FaceMode::Surprised;
  if (blinkAllowed && static_cast<int32_t>(now - nextBlinkAt) >= 0) {
    blinkUntil = now + 130;
    nextBlinkAt = now + 3200 + (now % 1700);
  }
  const bool blinking = blinkAllowed && blinkUntil != 0 &&
                         static_cast<int32_t>(blinkUntil - now) > 0;
  const bool animated = pcmPlaying || currentFace == FaceMode::Speaking ||
                        currentFace == FaceMode::Thinking ||
                        currentFace == FaceMode::Listening;
  if (!faceDirty && !animated && blinking == wasBlinking) return;
  faceDirty = false;
  wasBlinking = blinking;

  lgfx::LGFXBase& screen = faceCanvasReady
      ? static_cast<lgfx::LGFXBase&>(faceCanvas)
      : static_cast<lgfx::LGFXBase&>(M5.Display);
  const int w = M5.Display.width();
  const int h = M5.Display.height();
  // Coordinates below use a 320x240 design grid; scale to the actual panel.
  const auto sx = [w](int x) { return x * w / 320; };
  const auto sy = [h](int y) { return y * h / 240; };
  const uint16_t black = 0x0000;
  const uint16_t cyan = 0x5FFF;
  const uint16_t amber = 0xFEA8;
  const uint16_t softBlue = 0x7DFF;
  uint16_t ink = cyan;
  if (currentFace == FaceMode::Thinking || currentFace == FaceMode::Doubt ||
      currentFace == FaceMode::Proud) ink = amber;
  if (currentFace == FaceMode::Sad || currentFace == FaceMode::Sleepy) ink = softBlue;
  if (currentFace == FaceMode::Angry) ink = 0xFC88;
  screen.fillScreen(black);

  int leftY = 70, rightY = 70, leftH = 78, rightH = 78;
  int gaze = 0;
  switch (currentFace) {
    case FaceMode::Neutral: leftY = rightY = 82; leftH = rightH = 58; break;
    case FaceMode::Thinking:
      leftY = 68; leftH = 74; rightY = 96; rightH = 38;
      gaze = ((now / 700) % 2) ? 7 : -7;
      break;
    case FaceMode::Doubt: leftY = 62; leftH = 86; rightY = 103; rightH = 34; break;
    case FaceMode::Proud: leftY = 90; leftH = 43; rightY = 80; rightH = 58; break;
    case FaceMode::Sleepy: leftY = rightY = 120; leftH = rightH = 14; break;
    case FaceMode::Surprised: leftY = rightY = 52; leftH = rightH = 100; break;
    case FaceMode::Listening: leftY = rightY = 60; leftH = rightH = 92; break;
    default: break;
  }
  if (blinking) { leftY = rightY = 112; leftH = rightH = 12; }

  const int leftX = 38 + gaze, rightX = 184 + gaze;
  screen.fillRoundRect(sx(leftX), sy(leftY), sx(98), sy(leftH), sx(12), ink);
  screen.fillRoundRect(sx(rightX), sy(rightY), sx(98), sy(rightH), sx(12), ink);
  if (!blinking) {
    if (currentFace == FaceMode::Happy) {
      // Thick upside-down U's: visibly pleased without tiny pupils.
      screen.fillRoundRect(sx(leftX + 15), sy(104), sx(68), sy(68), sx(14), black);
      screen.fillRoundRect(sx(rightX + 15), sy(104), sx(68), sy(68), sx(14), black);
    } else if (currentFace == FaceMode::Angry) {
      screen.fillTriangle(sx(leftX), sy(68), sx(leftX + 99), sy(68),
                          sx(leftX + 99), sy(108), black);
      screen.fillTriangle(sx(rightX), sy(68), sx(rightX + 99), sy(68),
                          sx(rightX), sy(108), black);
    } else if (currentFace == FaceMode::Sad) {
      screen.fillTriangle(sx(leftX), sy(68), sx(leftX + 99), sy(68),
                          sx(leftX), sy(108), black);
      screen.fillTriangle(sx(rightX), sy(68), sx(rightX + 99), sy(68),
                          sx(rightX + 99), sy(108), black);
    }
  }

  if (pcmPlaying || currentFace == FaceMode::Speaking) {
    // Five wide segments respond to the PCM amplitude, never to random text.
    const int fullHeight = 10 + static_cast<int>(smoothedMouth * 36.0f);
    for (int i = 0; i < 5; ++i) {
      const int distance = i > 2 ? i - 2 : 2 - i;
      const int barH = fullHeight - distance * (fullHeight / 6);
      screen.fillRoundRect(sx(102 + i * 24), sy(190 - barH / 2),
                           sx(18), sy(barH), sx(3), ink);
    }
  } else if (currentFace == FaceMode::Thinking) {
    const int active = (now / 360) % 3;
    for (int i = 0; i < 3; ++i) {
      screen.fillRoundRect(sx(111 + i * 36), sy(i == active ? 178 : 185),
                           sx(26), sy(i == active ? 22 : 14), sx(4), ink);
    }
  } else if (currentFace == FaceMode::Surprised) {
    screen.fillRoundRect(sx(136), sy(172), sx(48), sy(40), sx(10), ink);
    screen.fillRoundRect(sx(149), sy(183), sx(22), sy(18), sx(4), black);
  } else if (currentFace == FaceMode::Happy) {
    screen.fillRoundRect(sx(110), sy(178), sx(100), sy(30), sx(12), ink);
    screen.fillRect(sx(122), sy(176), sx(76), sy(15), black);
  } else if (currentFace == FaceMode::Proud) {
    screen.fillRoundRect(sx(108), sy(187), sx(94), sy(13), sx(4), ink);
    screen.fillRoundRect(sx(191), sy(177), sx(16), sy(23), sx(4), ink);
  } else if (currentFace == FaceMode::Sad) {
    screen.fillRoundRect(sx(111), sy(179), sx(98), sy(28), sx(10), ink);
    screen.fillRect(sx(124), sy(193), sx(72), sy(18), black);
  } else {
    screen.fillRoundRect(sx(111), sy(183), sx(98), sy(13), sx(4), ink);
  }
  if (currentFace == FaceMode::Listening) {
    // A large, slow breathing pair of side marks is visible while waiting.
    const int markH = 24 + static_cast<int>((now / 240) % 3) * 8;
    screen.fillRoundRect(sx(10), sy(109 - markH / 2), sx(13), sy(markH), sx(3), ink);
    screen.fillRoundRect(sx(297), sy(109 - markH / 2), sx(13), sy(markH), sx(3), ink);
  }
  if (faceCanvasReady) faceCanvas.pushSprite(0, 0);
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
    lastCameraRestoreAttemptAt = millis();
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
  // BSP interpolates servo positions in its own task. This task schedules
  // finite targets and draws the face; camera ownership still gates M5 I2C.
  if (!cameraOwnsBus) M5StackChan.update();
  tickGesture();
  animateMouth();
  drawBlockFace();
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
    serviceTransport(false);
    if (transportLost) return -1;
    while (protocol.available() > 0) {
      const int ready = reader.put(static_cast<char>(protocol.read()));
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
  if (!checkTransportLink(true)) return false;
  const uint32_t crc = dataCrc32(data, count);
  protocol.printf("DATA %s %lu %08lx %u %u\n", format,
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
      serviceTransport(false);
      if (transportLost) { response = -1; break; }
      if (millis() - started >= kDataTotalTimeoutMs) {
        response = 0;
        break;
      }
      const size_t n = protocol.write(data + sent + written, block - written);
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
    protocol.println("DONE DATA");
    return true;
  }
  protocol.println(response == -1 ? "ERR DATA_CANCELLED" :
                 response == -2 ? "ERR DATA_ACK" : "ERR DATA_TIMEOUT");
  return false;
}

bool openCamera(unsigned width) {
  cancelGesture();
  // RGB565 DMA has a fixed expected frame byte count. Reconfigure the entire
  // driver on a resolution change; set_framesize alone is insufficient.
  if ((cameraReady && cameraWidth != width) || (!cameraReady && cameraOwnsBus)) {
    if (!closeCamera()) {
      protocol.println("ERR I2C_RESTORE");
      return false;
    }
  }
  if (!cameraReady) {
    if (!psramFound()) {
      protocol.println("ERR CAMERA_PSRAM");
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
      protocol.printf("ERR CAMERA_INIT 0x%lx\n", static_cast<unsigned long>(err));
      return false;
    }
    cameraReady = true;
    cameraWidth = width;
  }
  sensor_t* sensor = esp_camera_sensor_get();
  if (sensor == nullptr) {
    closeCamera();
    protocol.println("ERR CAMERA_SIZE");
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
    protocol.println("ERR CAMERA_FRAME");
    return;
  }
  esp_camera_fb_return(frame);
  frame = esp_camera_fb_get();
  if (frame == nullptr) {
    closeCamera();
    protocol.println("ERR CAMERA_FRAME");
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
    protocol.println("ERR CAMERA_JPEG");
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
  serviceTransport(false);
  if (transportLost) return true;
  while (protocol.available() > 0) {
    const int ready = reader.put(static_cast<char>(protocol.read()));
    if (!ready) continue;
    if (ready > 0 && strcmp(reader.text, "CANCEL") == 0) return true;
    if (ready < 0 || reader.text[0] != '\0') protocol.println("ERR MIC_BUSY");
  }
  return transportLost;
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
    protocol.println("ERR MIC_HEADER");
    return;
  }
  if (!closeCamera()) {
    protocol.println("ERR I2C_RESTORE");
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
      // An empty TOUCH poll is still idle time. Let a muted reply finish its
      // short gesture instead of cancelling it before the first visible step.
      tickGesture();
      drawBlockFace();
      if (micCancelRequested(control)) {
        cancelGesture();
        protocol.println("MIC CANCELLED");
        return;
      }
      if (touchedNow()) { touched = true; break; }
      delay(3);
    }
    if (!touched) {
      protocol.println("MIC EMPTY");
      return;
    }
  }
  // Actual capture (touch accepted or VAD armed) takes priority over gestures.
  cancelGesture();
  const size_t maxSamples = (maxMs * kSampleRate) / 1000;
  int16_t* clip = static_cast<int16_t*>(
      heap_caps_malloc(maxSamples * sizeof(int16_t),
                       MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (clip == nullptr) {
    protocol.println("ERR MIC_PSRAM");
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
    protocol.println("ERR MIC_INIT");
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
  setFace(FaceMode::Listening);

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
      setFace(FaceMode::Listening);
      protocol.println("MIC STARTED");
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
    protocol.println("MIC CANCELLED");
  } else if (failed) {
    protocol.println("ERR MIC_CAPTURE");
  } else if (!restored) {
    protocol.println("ERR SPEAKER_RESTORE");
  } else if (!recording || voicedFrames < 8 || lastVoiceEnd == 0) {
    protocol.println("MIC EMPTY");
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
  smoothedMouth = 0.0f;
  faceDirty = true;
  drawBlockFace();
  protocol.println("AUDIO DONE");
}

void failAudio(const char* reason) {
  // Called for failed receive or failed playRaw. Do not invoke during playback.
  const size_t received = wireReceived;
  const size_t expected = wireExpected;
  cancelGesture();
  clearPcm();
  smoothedMouth = 0.0f;
  faceDirty = true;
  drawBlockFace();
  protocol.print("ERR ");
  protocol.print(reason);
  if (strcmp(reason, "AUDIO_RX_TIMEOUT") == 0) {
    protocol.print(" received=");
    protocol.print(static_cast<unsigned long>(received));
    protocol.print(" expected=");
    protocol.print(static_cast<unsigned long>(expected));
  }
  protocol.println();
  // A failed binary transfer has no reliable line boundary left. Drop all
  // remaining TCP bytes instead of allowing them into the command parser.
  if (tcpOwner) markTransportLost();
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
  if (!checkTransportLink(true)) return;
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
  pcmPlaying = true;
  smoothedMouth = 0.0f;
  faceDirty = true;
  playbackStartAt = millis();
  playbackDurationMs = static_cast<uint32_t>((sampleCount * 1000ULL) / kSampleRate);
  lastLipAt = 0;
  protocol.println("AUDIO STARTED");
  startSpeechGesture();
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
  if (!checkTransportLink()) return;
  tickGesture();
  const int available = protocol.available();
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
    n = protocol.readBytes(reinterpret_cast<char*>(packet), toRead);
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
    n = protocol.readBytes(reinterpret_cast<char*>(pcmBuffer + pcmReceived), toRead);
    if (n == 0) return;
    crcUpdate(pcmBuffer + pcmReceived, n);
    pcmReceived += n;
  }
  wireReceived += n;
  lastRxAt = millis();
  if ((wireReceived % ackChunkBytes) == 0 || wireReceived == wireExpected) {
    protocol.print("ACK ");
    protocol.println(static_cast<unsigned long>(wireReceived));
  }
  if (wireReceived == wireExpected) {
    if (pcmReceived != pcmExpected) failAudio("AUDIO_DECODE_SIZE");
    else startPlayback();
  }
}

void startReceive(const char* args, bool compressed, bool fast = false) {
  if (pcmReceiving || pcmPlaying) {
    cancelGesture();
    protocol.println("ERR AUDIO_BUSY");
    return;
  }
  if (fast && !fastAudioReady && !tcpOwner) {
    cancelGesture();
    protocol.println("ERR AUDIO_RX_BUFFER");
    return;
  }

  // AUDIO <pcm_bytes> <CRC32-of-PCM16> (legacy)
  // AUDIO4 <sample_count> <CRC32-of-ADPCM4> (4x smaller transport)
  char* sizeEnd = nullptr;
  const unsigned long count = strtoul(args, &sizeEnd, 10);
  if (sizeEnd == args || *sizeEnd != ' ') {
    cancelGesture();
    protocol.println("ERR AUDIO_HEADER");
    return;
  }
  const char* crcText = sizeEnd + 1;
  if (strlen(crcText) != 8) {
    cancelGesture();
    protocol.println("ERR AUDIO_HEADER");
    return;
  }
  for (size_t i = 0; i < 8; ++i) {
    const char ch = crcText[i];
    if (!((ch >= '0' && ch <= '9') || (ch >= 'A' && ch <= 'F') ||
          (ch >= 'a' && ch <= 'f'))) {
      cancelGesture();
      protocol.println("ERR AUDIO_HEADER");
      return;
    }
  }
  const uint32_t crcValue = static_cast<uint32_t>(strtoul(crcText, nullptr, 16));
  if (count == 0 ||
      (compressed ? count > kMaxPcmBytes / sizeof(int16_t)
                  : count > kMaxPcmBytes || (count & 1U) != 0)) {
    cancelGesture();
    protocol.println("ERR AUDIO_SIZE");
    return;
  }
  if (!ensureSpeaker()) {
    cancelGesture();
    protocol.println("ERR SPEAKER_UNAVAILABLE");
    return;
  }

  const size_t pcmBytes = compressed ? static_cast<size_t>(count) * 2U
                                     : static_cast<size_t>(count);
  const size_t incomingBytes = compressed ? (static_cast<size_t>(count) + 1U) / 2U
                                          : static_cast<size_t>(count);
  pcmBuffer = static_cast<uint8_t*>(
      heap_caps_malloc(pcmBytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (pcmBuffer == nullptr) {
    cancelGesture();
    protocol.println("ERR AUDIO_PSRAM");
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
  protocol.println("READY AUDIO");
}

void handleLine(const char* line) {
  if (strcmp(line, "PING") == 0) {
    protocol.println("PONG");
    return;
  }
  if (strcmp(line, "INFO") == 0) {
    protocol.print((fastAudioReady || tcpOwner)
        ? "INFO FRISTON2_STACKCHAN_V4_AUDIO_PCM16_16K_ACK128_ADPCM4_ACK1024 CAMERA_JPEG_V1 MIC_PCM16_V1 SERVO_MOTION_V1 SERVO_TORQUE_FIX_V3 BLOCK_FACE_V1 FACE_AUDIO_HOLD_V1 GESTURE_V1 GESTURE_V2"
        : "INFO FRISTON2_STACKCHAN_V4_AUDIO_PCM16_16K_ACK128_ADPCM4 CAMERA_JPEG_V1 MIC_PCM16_V1 SERVO_MOTION_V1 SERVO_TORQUE_FIX_V3 BLOCK_FACE_V1 FACE_AUDIO_HOLD_V1 GESTURE_V1 GESTURE_V2");
    if (wifiReady) protocol.print(" WIFI_TCP_V1");
    protocol.println();
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
  // STOP is available during playback and never frees the speaker's buffer.
  if (strcmp(line, "STOP") == 0) {
    cancelGesture();
    if (cameraOwnsBus && !closeCamera()) { protocol.println("ERR I2C_RESTORE"); return; }
    M5StackChan.Motion.stop();
    protocol.println("OK STOP");
    return;
  }
  if (strncmp(line, "GESTURE ", 8) == 0) {
    startGesture(line + 8);
    return;
  }
  if (strcmp(line, "HOME") == 0 || strncmp(line, "MOVE ", 5) == 0 ||
      strcmp(line, "CANCEL") == 0) cancelGesture();
  if (pcmPlaying) {
    protocol.println("ERR AUDIO_BUSY");
    return;
  }
  if (strcmp(line, "CANCEL") == 0) {
    closeCamera();
    protocol.println("OK CANCEL");
    return;
  }
  if (strcmp(line, "HOME") == 0) {
    if (cameraOwnsBus && !closeCamera()) { protocol.println("ERR I2C_RESTORE"); return; }
    // BSP Servo::init() disables torque. Moving the animation target alone
    // does not enable it. Suppress auto-release during target/torque setup,
    // then restore the BSP's normal idle release behavior.
    M5StackChan.Motion.setAutoTorqueReleaseEnabled(false);
    M5StackChan.Motion.goHome(200);
    M5StackChan.Motion.setTorqueEnabled(true);
    M5StackChan.Motion.setAutoTorqueReleaseEnabled(true);
    protocol.println("OK HOME");
    return;
  }
  if (strncmp(line, "MOVE ", 5) == 0) {
    if (cameraOwnsBus && !closeCamera()) { protocol.println("ERR I2C_RESTORE"); return; }
    long x = 0, y = 0, speed = 300;
    char extra = 0;
    const int fields = sscanf(line + 5, "%ld %ld %ld %c", &x, &y, &speed, &extra);
    // StackChan BSP uses tenths of degrees. Keep Y away from mechanical stops.
    if (fields != 3 || x < -900 || x > 900 || y < 50 || y > 850 ||
        speed < 100 || speed > 800) {
      protocol.println("ERR MOVE_RANGE");
      return;
    }
    // Atomically set both targets through Motion's mutex. Do not energize
    // torque before setting the new targets, or a stale target could move us.
    M5StackChan.Motion.setAutoTorqueReleaseEnabled(false);
    M5StackChan.Motion.move(static_cast<int>(x), static_cast<int>(y),
                           static_cast<int>(speed));
    M5StackChan.Motion.setTorqueEnabled(true);
    M5StackChan.Motion.setAutoTorqueReleaseEnabled(true);
    protocol.printf("OK MOVE %ld %ld %ld\n", x, y, speed);
    return;
  }
  if (strcmp(line, "CAMERA CLOSE") == 0) {
    cancelGesture();
    protocol.println(closeCamera() ? "OK CAMERA CLOSE" : "ERR I2C_RESTORE");
    return;
  }
  if (strncmp(line, "CAMERA ", 7) == 0) {
    if (strcmp(line + 7, "320") == 0) captureCamera(320);
    else if (strcmp(line + 7, "640") == 0) captureCamera(640);
    else protocol.println("ERR CAMERA_SIZE");
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
      protocol.println("ERR VOLUME_RANGE");
      return;
    }
    // Retain volume across Mic/Speaker I2S handovers. No codec/I2C access is
    // needed here while the camera owns the bus; AUDIO restores the speaker.
    speakerVolume = static_cast<uint8_t>(level);
    if (speakerReady && !cameraOwnsBus) M5.Speaker.setVolume(speakerVolume);
    protocol.print("OK VOLUME ");
    protocol.println(level);
    return;
  }
  if (strncmp(line, "FACE ", 5) == 0) {
    const char* requested = line + 5;
    for (const auto& entry : kFaces) {
      if (strcmp(requested, entry.key) == 0) {
        if (currentFace != entry.value) setFace(entry.value);
        protocol.print("OK FACE ");
        protocol.println(entry.key);
        return;
      }
    }
    protocol.println("ERR UNKNOWN_FACE");
    return;
  }
  protocol.println("ERR UNKNOWN_COMMAND");
}

void pollProtocol() {
  if (transportLost) return;
  while (protocol.available() > 0) {
    if (pcmReceiving) {
      receivePcm();
      continue;
    }
    const int next = protocol.read();
    if (next < 0) return;
    const char ch = static_cast<char>(next);
    if (ch == '\r') continue;
    if (ch == '\n') {
      if (lineTooLong) {
        protocol.println("ERR LINE_TOO_LONG");
      } else if (lineUsed > 0) {
        lineBuffer[lineUsed] = '\0';
        commandActive = true;
        handleLine(lineBuffer);
        commandActive = false;
      }
      lineUsed = 0;
      lineTooLong = false;
      if (transportLost) return;
      continue;
    }
    if (lineTooLong) continue;
    if (lineUsed + 1 < kLineMax) lineBuffer[lineUsed++] = ch;
    else lineTooLong = true;
  }
}

void animateMouth() {
  if (!pcmPlaying && currentFace != FaceMode::Speaking) return;
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
  } else {
    // Preserve v0.3's manual FACE SPEAKING animation.
    const float seq[] = {0.12f, 0.60f, 0.88f, 0.27f, 0.75f, 0.18f};
    smoothedMouth = seq[lipStep];
    lipStep = (lipStep + 1) % (sizeof(seq) / sizeof(seq[0]));
  }
}

void pollAudioState() {
  if (transportLost) return;  // Disconnect cleanup alone owns the PCM buffer.
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
  protocol.setTimeout(30);
#if ARDUINO_USB_MODE && ARDUINO_USB_CDC_ON_BOOT
  if (Serial.setTxBufferSize(4096) < 4096) Serial.setTxBufferSize(256);
#endif
  speakerReady = M5.Speaker.begin();
  if (speakerReady) M5.Speaker.setVolume(speakerVolume);
  faceCanvas.setColorDepth(8);
  faceCanvasReady = faceCanvas.createSprite(M5.Display.width(), M5.Display.height()) != nullptr;
  setFace(FaceMode::Idle);
  // Do not move on boot or serial reconnect. Motion starts on HOME/MOVE/GESTURE.
  // BSP initializes torque OFF; command handlers explicitly enable it.
  M5StackChan.Motion.setAutoTorqueReleaseEnabled(true);
  Serial.println("READY FRISTON2_STACKCHAN_AUDIO");
  if (!speakerReady) Serial.println("WARN SPEAKER_INIT_FAILED");
  if (!faceCanvasReady) Serial.println("WARN FACE_CANVAS_DIRECT_DRAW");
  startWireless();
}

void loop() {
  serviceTransport(true);
  serviceUi();
  pollProtocol();
  pollAudioState();
  animateMouth();
  if (cameraReady && millis() - lastCameraAt > kCameraIdleMs) closeCamera();
  if (cameraOwnsBus && !cameraReady &&
      millis() - lastCameraRestoreAttemptAt >= kCameraRestoreRetryMs) {
    // Transient I2C restoration failure must not strand TCP behind its busy
    // gate forever. Each attempt is separated; USB recovery stays available.
    closeCamera();
  }
  delay(3);
}
