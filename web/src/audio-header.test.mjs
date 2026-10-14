import assert from "node:assert/strict";
import test from "node:test";
import { UTA1_HEADER_BYTES, decodeAudioChunk, encodeAudioChunk, pcm16ToFloat32 } from "./audio-header.ts";

test("UTA1 header roundtrip preserves fields and samples", () => {
  const samples = new Int16Array([0, 1, -1, 32767, -32768, 1234, -4321]);
  const buffer = encodeAudioChunk({ replySeq: 0xfffffffe, chunkIndex: 7, sampleRate: 24000 }, samples);
  assert.equal(buffer.byteLength, UTA1_HEADER_BYTES + samples.length * 2);
  const decoded = decodeAudioChunk(buffer);
  assert.equal(decoded.ok, true);
  assert.equal(decoded.chunk.replySeq, 0xfffffffe);
  assert.equal(decoded.chunk.chunkIndex, 7);
  assert.equal(decoded.chunk.sampleRate, 24000);
  assert.deepEqual([...decoded.chunk.samples], [...samples]);
});

test("header layout is magic + three little-endian uint32 values", () => {
  const buffer = encodeAudioChunk({ replySeq: 0x01020304, chunkIndex: 2, sampleRate: 16000 }, new Int16Array([0x0102]));
  const bytes = [...new Uint8Array(buffer)];
  assert.deepEqual(bytes.slice(0, 4), [0x55, 0x54, 0x41, 0x31]); // "UTA1"
  assert.deepEqual(bytes.slice(4, 8), [0x04, 0x03, 0x02, 0x01]);
  assert.deepEqual(bytes.slice(8, 12), [2, 0, 0, 0]);
  assert.deepEqual(bytes.slice(12, 16), [0x80, 0x3e, 0, 0]);
  assert.deepEqual(bytes.slice(16), [0x02, 0x01]);
});

test("decoder works on unaligned views and copies the samples", () => {
  const inner = encodeAudioChunk({ replySeq: 1, chunkIndex: 0, sampleRate: 22050 }, new Int16Array([5, -6, 7]));
  const outer = new Uint8Array(inner.byteLength + 1);
  outer.set(new Uint8Array(inner), 1);
  const decoded = decodeAudioChunk(new Uint8Array(outer.buffer, 1, inner.byteLength));
  assert.equal(decoded.ok, true);
  assert.deepEqual([...decoded.chunk.samples], [5, -6, 7]);
  const aligned = decodeAudioChunk(inner);
  new Uint8Array(inner).fill(0);
  assert.deepEqual([...aligned.chunk.samples], [5, -6, 7]);
});

test("decoder rejects malformed frames", () => {
  assert.deepEqual(decodeAudioChunk(new ArrayBuffer(10)), { ok: false, error: "too_short" });
  const bad = new Uint8Array(encodeAudioChunk({ replySeq: 1, chunkIndex: 0, sampleRate: 24000 }, new Int16Array(2)));
  bad[0] = 0x58;
  assert.deepEqual(decodeAudioChunk(bad), { ok: false, error: "bad_magic" });
  const odd = new Uint8Array(UTA1_HEADER_BYTES + 3);
  odd.set(new Uint8Array(encodeAudioChunk({ replySeq: 1, chunkIndex: 0, sampleRate: 24000 }, new Int16Array(0))));
  assert.deepEqual(decodeAudioChunk(odd), { ok: false, error: "odd_payload_length" });
  const rate = new Uint8Array(encodeAudioChunk({ replySeq: 1, chunkIndex: 0, sampleRate: 24000 }, new Int16Array(1)));
  new DataView(rate.buffer).setUint32(12, 1000, true);
  assert.deepEqual(decodeAudioChunk(rate), { ok: false, error: "sample_rate_invalid" });
  const empty = decodeAudioChunk(encodeAudioChunk({ replySeq: 3, chunkIndex: 1, sampleRate: 24000 }, new Int16Array(0)));
  assert.equal(empty.ok, true);
  assert.equal(empty.chunk.samples.length, 0);
});

test("encoder validates its inputs", () => {
  assert.throws(() => encodeAudioChunk({ replySeq: -1, chunkIndex: 0, sampleRate: 24000 }, new Int16Array(1)), /reply_seq/);
  assert.throws(() => encodeAudioChunk({ replySeq: 0, chunkIndex: 0.5, sampleRate: 24000 }, new Int16Array(1)), /chunk_index/);
  assert.throws(() => encodeAudioChunk({ replySeq: 0, chunkIndex: 0, sampleRate: 500 }, new Int16Array(1)), /sample_rate/);
});

test("PCM16 converts to float in [-1, 1)", () => {
  assert.deepEqual([...pcm16ToFloat32(new Int16Array([-32768, 0, 16384]))], [-1, 0, 0.5]);
});
