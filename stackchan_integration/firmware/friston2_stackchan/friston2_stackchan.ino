// Friston-2 x StackChan K151 / LCD MODULE E | ADPCM4 ACK1024 + legacy ACK128
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
// v0.4 does not operate the servos, microphone, camera, or Wi-Fi.

#include <Arduino.h>
#include <Avatar.h>
#include <M5Unified.h>
#include <esp_heap_caps.h>
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
  if (!speakerReady) {
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
        ? "INFO FRISTON2_STACKCHAN_V4_AUDIO_PCM16_16K_ACK128_ADPCM4_ACK1024"
        : "INFO FRISTON2_STACKCHAN_V4_AUDIO_PCM16_16K_ACK128_ADPCM4");
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
  if (strncmp(line, "VOLUME ", 7) == 0) {
    char* end = nullptr;
    unsigned long level = strtoul(line + 7, &end, 10);
    if (end == line + 7 || *end != '\0' || level > 160) {
      Serial.println("ERR VOLUME_RANGE");
      return;
    }
    M5.Speaker.setVolume(static_cast<uint8_t>(level));
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
  auto cfg = M5.config();
  M5.begin(cfg);
  M5.Display.setRotation(1);
  M5.Display.fillScreen(TFT_BLACK);
  fastAudioReady = Serial.setRxBufferSize(4096) >= 4096;
  Serial.begin(115200);
  speakerReady = M5.Speaker.begin();
  if (speakerReady) M5.Speaker.setVolume(80);  // Initial low/medium volume.
  avatar.init(1);
  avatar.setIsAutoBlink(true);
  avatar.setSpeechText("");
  setFace(FaceMode::Idle);
  Serial.println("READY FRISTON2_STACKCHAN_AUDIO");
  if (!speakerReady) Serial.println("WARN SPEAKER_INIT_FAILED");
}

void loop() {
  M5.update();
  pollSerial();
  pollAudioState();
  animateMouth();
  delay(3);
}
