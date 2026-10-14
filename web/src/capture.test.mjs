import assert from "node:assert/strict";
import test from "node:test";
import { buildAudioConstraints, classifyMediaError, micSupport, rmsToMeter } from "./capture.ts";

// --- Load the real AudioWorklet file with a tiny fake AudioWorkletGlobalScope. ---
let Processor = null;
let registeredName = null;
globalThis.AudioWorkletProcessor = class {
  constructor() {
    this.port = {
      messages: [],
      postMessage(message) {
        this.messages.push(message);
      },
    };
  }
};
globalThis.registerProcessor = (name, cls) => {
  registeredName = name;
  Processor = cls;
};
globalThis.sampleRate = 16000;
await import(new URL("../public/capture-worklet.js", import.meta.url).href);

function makeProcessor(rate) {
  globalThis.sampleRate = rate;
  return new Processor();
}

/** Push `seconds` of a sine (or sum of sines) through the worklet in 128-sample render quanta. */
function feed(processor, rate, seconds, freqs, amplitude = 0.5, channels = 1) {
  const total = Math.round(rate * seconds);
  for (let offset = 0; offset < total; offset += 128) {
    const block = new Float32Array(Math.min(128, total - offset));
    for (let i = 0; i < block.length; i += 1) {
      const t = (offset + i) / rate;
      block[i] = freqs.reduce((sum, f) => sum + amplitude * Math.sin(2 * Math.PI * f * t), 0);
    }
    processor.process([Array.from({ length: channels }, () => block)]);
  }
  return processor.port.messages.filter((message) => message.type === "frame");
}

function pcmOf(frames) {
  const out = [];
  for (const frame of frames) out.push(...new Int16Array(frame.pcm));
  return Float32Array.from(out, (sample) => sample / 32768);
}

/** RMS after skipping the filter warm-up. */
function steadyRms(samples, skip = 400) {
  let sum = 0;
  let count = 0;
  for (let i = skip; i < samples.length; i += 1) {
    sum += samples[i] * samples[i];
    count += 1;
  }
  return Math.sqrt(sum / count);
}

/** Amplitude of one frequency component (single-bin DFT). */
function toneAmplitude(samples, freq, rate, skip = 400) {
  let re = 0;
  let im = 0;
  let n = 0;
  for (let i = skip; i < samples.length; i += 1) {
    re += samples[i] * Math.cos((2 * Math.PI * freq * i) / rate);
    im -= samples[i] * Math.sin((2 * Math.PI * freq * i) / rate);
    n += 1;
  }
  return (2 * Math.hypot(re, im)) / n;
}

test("worklet registers under the expected name and reports its mode", () => {
  assert.equal(registeredName, "utterlane-capture");
  const native = makeProcessor(16000);
  assert.deepEqual(native.port.messages[0], { type: "ready", inputRate: 16000, resampling: false });
  const resampling = makeProcessor(48000);
  assert.equal(resampling.port.messages[0].resampling, true);
});

test("16 kHz passthrough emits 640-byte PCM16 frames", () => {
  const processor = makeProcessor(16000);
  const frames = feed(processor, 16000, 0.2, [440]);
  assert.equal(frames.length, 10); // 3200 samples / 320
  for (const frame of frames) assert.equal(frame.pcm.byteLength, 640);
  const pcm = pcmOf(frames);
  assert.ok(Math.abs(steadyRms(pcm, 0) - 0.5 / Math.SQRT2) < 0.01);
  assert.ok(frames[3].rms > 0.3 && frames[3].rms < 0.4);
});

test("48 kHz → 16 kHz keeps the speech band and the frame rate", () => {
  const processor = makeProcessor(48000);
  const frames = feed(processor, 48000, 1, [1000]);
  // One second in → ~50 frames out (minus the filter's 48-sample look-ahead).
  assert.ok(frames.length >= 49 && frames.length <= 50, `frames=${frames.length}`);
  const pcm = pcmOf(frames);
  const amplitude = toneAmplitude(pcm, 1000, 16000);
  assert.ok(Math.abs(amplitude - 0.5) < 0.02, `1 kHz amplitude ${amplitude}`);
});

test("48 kHz → 16 kHz low-passes before decimating (no aliasing)", () => {
  // A 12 kHz tone would alias to 4 kHz with naive decimation.
  const processor = makeProcessor(48000);
  const pcm = pcmOf(feed(processor, 48000, 1, [12000]));
  const alias = toneAmplitude(pcm, 4000, 16000);
  assert.ok(alias < 0.5 * 0.005, `alias amplitude ${alias} (should be < -46 dB)`);
  assert.ok(steadyRms(pcm) < 0.005);
});

test("44.1 kHz → 16 kHz handles a fractional ratio", () => {
  const processor = makeProcessor(44100);
  const frames = feed(processor, 44100, 1, [700, 3000], 0.3);
  assert.ok(frames.length >= 49 && frames.length <= 50, `frames=${frames.length}`);
  const pcm = pcmOf(frames);
  assert.ok(Math.abs(toneAmplitude(pcm, 700, 16000) - 0.3) < 0.02);
  assert.ok(Math.abs(toneAmplitude(pcm, 3000, 16000) - 0.3) < 0.02);
  // 10 kHz would alias to 6 kHz.
  const aliasProcessor = makeProcessor(44100);
  const aliasPcm = pcmOf(feed(aliasProcessor, 44100, 1, [10000]));
  assert.ok(toneAmplitude(aliasPcm, 6000, 16000) < 0.5 * 0.01);
});

test("stereo input is downmixed and samples are clamped", () => {
  const processor = makeProcessor(16000);
  const frames = feed(processor, 16000, 0.04, [500], 2.0, 2);
  const samples = frames.flatMap((frame) => [...new Int16Array(frame.pcm)]);
  assert.equal(Math.max(...samples), 32767);
  assert.equal(Math.min(...samples), -32768);
});

test("media errors map to friendly categories", () => {
  assert.equal(classifyMediaError({ name: "NotAllowedError" }), "denied");
  assert.equal(classifyMediaError({ name: "SecurityError" }), "denied");
  assert.equal(classifyMediaError({ name: "NotFoundError" }), "no_device");
  assert.equal(classifyMediaError({ name: "OverconstrainedError" }), "no_device");
  assert.equal(classifyMediaError({ name: "NotReadableError" }), "busy");
  assert.equal(classifyMediaError(new TypeError("x")), "unsupported");
  assert.equal(classifyMediaError("weird"), "unknown");
});

test("constraints request voice processing and a pinned device when chosen", () => {
  const base = buildAudioConstraints(null);
  assert.equal(base.echoCancellation, true);
  assert.equal(base.noiseSuppression, true);
  assert.equal(base.autoGainControl, true);
  assert.deepEqual(base.channelCount, { ideal: 1 });
  assert.equal("deviceId" in base, false);
  assert.deepEqual(buildAudioConstraints("abc").deviceId, { exact: "abc" });
});

test("meter mapping and capability checks", () => {
  assert.equal(rmsToMeter(0), 0);
  assert.equal(rmsToMeter(0.0005), 0);
  assert.equal(rmsToMeter(1), 1);
  const mid = rmsToMeter(0.03);
  assert.ok(mid > 0.3 && mid < 0.6);
  assert.equal(micSupport({ isSecureContext: false, mediaDevices: {}, AudioWorkletNode: class {} }), "insecure");
  assert.equal(micSupport({ isSecureContext: true, AudioWorkletNode: class {} }), "unsupported");
  assert.equal(micSupport({ isSecureContext: true, mediaDevices: {}, AudioWorkletNode: class {} }), null);
});
