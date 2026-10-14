// UTA1 assistant-audio frame: 16-byte little-endian header followed by PCM16LE mono.
//   0  4 bytes ASCII  magic "UTA1"
//   4  uint32         reply_seq
//   8  uint32         chunk_index
//  12  uint32         sample_rate
//  16  int16[]        samples

export const UTA1_MAGIC = "UTA1";
export const UTA1_HEADER_BYTES = 16;
export const MIN_SAMPLE_RATE = 8000;
export const MAX_SAMPLE_RATE = 96000;

export interface AudioChunkHeader {
  replySeq: number;
  chunkIndex: number;
  sampleRate: number;
}

export interface AudioChunk extends AudioChunkHeader {
  samples: Int16Array;
}

export type DecodeResult = { ok: true; chunk: AudioChunk } | { ok: false; error: string };

const MAGIC_BYTES = [0x55, 0x54, 0x41, 0x31]; // "UTA1"

const LITTLE_ENDIAN_HOST = new Uint8Array(new Uint16Array([1]).buffer)[0] === 1;

function isUint32(value: number): boolean {
  return Number.isInteger(value) && value >= 0 && value <= 0xffffffff;
}

export function encodeAudioChunk(header: AudioChunkHeader, samples: Int16Array): ArrayBuffer {
  if (!isUint32(header.replySeq)) throw new RangeError("reply_seq_invalid");
  if (!isUint32(header.chunkIndex)) throw new RangeError("chunk_index_invalid");
  if (!isUint32(header.sampleRate) || header.sampleRate < MIN_SAMPLE_RATE || header.sampleRate > MAX_SAMPLE_RATE) {
    throw new RangeError("sample_rate_invalid");
  }
  const buffer = new ArrayBuffer(UTA1_HEADER_BYTES + samples.length * 2);
  const view = new DataView(buffer);
  MAGIC_BYTES.forEach((byte, index) => view.setUint8(index, byte));
  view.setUint32(4, header.replySeq, true);
  view.setUint32(8, header.chunkIndex, true);
  view.setUint32(12, header.sampleRate, true);
  for (let index = 0; index < samples.length; index += 1) {
    view.setInt16(UTA1_HEADER_BYTES + index * 2, samples[index], true);
  }
  return buffer;
}

export function decodeAudioChunk(data: ArrayBuffer | ArrayBufferView): DecodeResult {
  const bytes =
    data instanceof ArrayBuffer
      ? new Uint8Array(data)
      : new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
  if (bytes.byteLength < UTA1_HEADER_BYTES) return { ok: false, error: "too_short" };
  for (let index = 0; index < 4; index += 1) {
    if (bytes[index] !== MAGIC_BYTES[index]) return { ok: false, error: "bad_magic" };
  }
  const payloadBytes = bytes.byteLength - UTA1_HEADER_BYTES;
  if (payloadBytes % 2 !== 0) return { ok: false, error: "odd_payload_length" };
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const replySeq = view.getUint32(4, true);
  const chunkIndex = view.getUint32(8, true);
  const sampleRate = view.getUint32(12, true);
  if (sampleRate < MIN_SAMPLE_RATE || sampleRate > MAX_SAMPLE_RATE) return { ok: false, error: "sample_rate_invalid" };

  const count = payloadBytes / 2;
  let samples: Int16Array;
  const offset = bytes.byteOffset + UTA1_HEADER_BYTES;
  if (LITTLE_ENDIAN_HOST && offset % 2 === 0) {
    // Copy so the chunk does not pin the socket's receive buffer.
    samples = new Int16Array(bytes.buffer.slice(offset, offset + payloadBytes));
  } else {
    samples = new Int16Array(count);
    for (let index = 0; index < count; index += 1) {
      samples[index] = view.getInt16(UTA1_HEADER_BYTES + index * 2, true);
    }
  }
  return { ok: true, chunk: { replySeq, chunkIndex, sampleRate, samples } };
}

export function pcm16ToFloat32(samples: Int16Array, out?: Float32Array): Float32Array {
  const target = out ?? new Float32Array(samples.length);
  for (let index = 0; index < samples.length; index += 1) target[index] = samples[index] / 32768;
  return target;
}
