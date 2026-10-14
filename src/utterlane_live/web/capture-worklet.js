// Utterlane Live microphone capture worklet.
//
// Input: whatever rate the AudioContext runs at (ideally 16 kHz).
// Output: posts { type: "frame", pcm: ArrayBuffer(640 bytes), rms } messages, each a
// 20 ms frame of 320 signed 16-bit little-endian mono samples at 16 kHz.
//
// When the context is not at 16 kHz, the signal is resampled with a band-limited
// polyphase windowed-sinc filter (Blackman window, 96 taps, cutoff 7 kHz) evaluated at
// fractional input positions. That low-pass removes everything above the new Nyquist
// frequency before decimation, so there is no aliasing.

const TARGET_RATE = 16000;
const FRAME_SAMPLES = 320;
const TAPS = 96;
const HALF = TAPS / 2;
const PHASES = 256;
const CUTOFF_HZ = 7000;

function buildKernelTable(inputRate) {
  const fc = CUTOFF_HZ / inputRate; // cycles per input sample
  const table = new Float32Array(PHASES * TAPS);
  for (let phase = 0; phase < PHASES; phase += 1) {
    const frac = phase / PHASES;
    let sum = 0;
    for (let tap = 0; tap < TAPS; tap += 1) {
      // Distance (in input samples) from the output instant to this input sample.
      const x = tap - (HALF - 1) - frac;
      const u = 2 * fc * x;
      const sinc = u === 0 ? 1 : Math.sin(Math.PI * u) / (Math.PI * u);
      const window =
        0.42 + 0.5 * Math.cos((Math.PI * x) / HALF) + 0.08 * Math.cos((2 * Math.PI * x) / HALF);
      const value = 2 * fc * sinc * Math.max(0, window);
      table[phase * TAPS + tap] = value;
      sum += value;
    }
    // Unity DC gain for every phase.
    for (let tap = 0; tap < TAPS; tap += 1) table[phase * TAPS + tap] /= sum;
  }
  return table;
}

class UtterlaneCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.inputRate = sampleRate;
    this.passthrough = Math.abs(this.inputRate - TARGET_RATE) < 1;
    this.ratio = this.inputRate / TARGET_RATE; // input samples per output sample
    this.kernel = this.passthrough ? null : buildKernelTable(this.inputRate);
    // work = [history (TAPS samples) | current block]
    this.work = new Float32Array(TAPS + 128);
    this.mono = new Float32Array(128);
    this.t = TAPS; // next output instant, in work-buffer sample units
    this.frame = new Int16Array(FRAME_SAMPLES);
    this.frameIndex = 0;
    this.squareSum = 0;
    this.port.postMessage({ type: "ready", inputRate: this.inputRate, resampling: !this.passthrough });
  }

  pushSample(value) {
    const s = value > 1 ? 1 : value < -1 ? -1 : value;
    this.frame[this.frameIndex] = Math.round(s < 0 ? s * 32768 : s * 32767);
    this.squareSum += s * s;
    this.frameIndex += 1;
    if (this.frameIndex === FRAME_SAMPLES) {
      const pcm = this.frame.buffer;
      const rms = Math.sqrt(this.squareSum / FRAME_SAMPLES);
      this.port.postMessage({ type: "frame", pcm, rms }, [pcm]);
      this.frame = new Int16Array(FRAME_SAMPLES);
      this.frameIndex = 0;
      this.squareSum = 0;
    }
  }

  downmix(channels, length) {
    if (this.mono.length < length) this.mono = new Float32Array(length);
    const mono = this.mono;
    const count = channels.length;
    if (count === 1) {
      mono.set(channels[0].subarray(0, length));
      return mono;
    }
    for (let i = 0; i < length; i += 1) {
      let sum = 0;
      for (let c = 0; c < count; c += 1) sum += channels[c][i];
      mono[i] = sum / count;
    }
    return mono;
  }

  resampleBlock(mono, length) {
    const needed = TAPS + length;
    if (this.work.length < needed) {
      const grown = new Float32Array(needed);
      grown.set(this.work.subarray(0, TAPS));
      this.work = grown;
    }
    const work = this.work;
    work.set(mono.subarray(0, length), TAPS);
    const kernel = this.kernel;
    let t = this.t;
    for (;;) {
      let center = Math.floor(t);
      let phase = Math.round((t - center) * PHASES);
      if (phase === PHASES) {
        phase = 0;
        center += 1;
      }
      if (center + HALF >= needed) break;
      const base = center - (HALF - 1);
      const offset = phase * TAPS;
      let acc = 0;
      for (let tap = 0; tap < TAPS; tap += 1) acc += work[base + tap] * kernel[offset + tap];
      this.pushSample(acc);
      t += this.ratio;
    }
    // Keep the last TAPS samples as history for the next block.
    work.copyWithin(0, length, needed);
    this.t = t - length;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0 || !input[0] || input[0].length === 0) return true;
    const length = input[0].length;
    const mono = this.downmix(input, length);
    if (this.passthrough) {
      for (let i = 0; i < length; i += 1) this.pushSample(mono[i]);
    } else {
      this.resampleBlock(mono, length);
    }
    return true;
  }
}

registerProcessor("utterlane-capture", UtterlaneCaptureProcessor);
