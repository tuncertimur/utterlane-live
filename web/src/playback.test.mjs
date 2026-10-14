import assert from "node:assert/strict";
import test from "node:test";
import { PlaybackScheduler } from "./playback.ts";

/** Minimal fake of the Web Audio pieces the scheduler touches. `currentTime` is the injectable clock. */
class FakeContext {
  currentTime = 0;
  sources = [];
  createBuffer(channels, length, sampleRate) {
    const data = new Float32Array(length);
    return { duration: length / sampleRate, sampleRate, getChannelData: () => data };
  }
  createBufferSource() {
    const source = {
      buffer: null,
      onended: null,
      startedAt: null,
      stopped: false,
      connectedTo: null,
      connect(node) {
        this.connectedTo = node;
      },
      start(when) {
        this.startedAt = when;
      },
      stop() {
        this.stopped = true;
      },
      disconnect() {},
      /** Simulate the browser finishing playback. */
      finish() {
        this.onended?.();
      },
    };
    this.sources.push(source);
    return source;
  }
}

function chunk(replySeq, chunkIndex, seconds, sampleRate = 24000) {
  return { replySeq, chunkIndex, sampleRate, samples: new Int16Array(Math.round(seconds * sampleRate)).fill(1000) };
}

function setup() {
  const context = new FakeContext();
  const events = [];
  const destination = { name: "gain" };
  const scheduler = new PlaybackScheduler({
    context,
    destination,
    leadInSec: 0.05,
    onEvent: (event) => events.push(`${event.state}:${event.replyId ?? event.replySeq}`),
  });
  return { context, events, scheduler, destination };
}

const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-9, `${actual} ≉ ${expected}`);

test("chunks of a reply are scheduled back to back without gaps", () => {
  const { context, events, scheduler, destination } = setup();
  context.currentTime = 10;
  scheduler.registerReply(1, "r-1");
  scheduler.enqueue(chunk(1, 0, 0.5));
  scheduler.enqueue(chunk(1, 1, 0.25));
  scheduler.enqueue(chunk(1, 2, 1));
  const starts = context.sources.map((source) => source.startedAt);
  near(starts[0], 10.05);
  near(starts[1], 10.55);
  near(starts[2], 10.8);
  near(scheduler.nextStartTime, 11.8);
  assert.equal(context.sources[0].connectedTo, destination);
  assert.ok(Math.abs(context.sources[0].buffer.getChannelData(0)[0] - 1000 / 32768) < 1e-6);
  assert.deepEqual(events, ["started:r-1"]);
});

test("out-of-order chunks are played in chunk_index order", () => {
  const { context, scheduler } = setup();
  scheduler.registerReply(7, "r-7");
  scheduler.enqueue(chunk(7, 1, 0.3));
  assert.equal(context.sources.length, 0, "chunk 1 waits for chunk 0");
  scheduler.enqueue(chunk(7, 0, 0.2));
  scheduler.enqueue(chunk(7, 0, 0.2)); // duplicate is ignored
  assert.equal(context.sources.length, 2);
  near(context.sources[0].buffer.duration, 0.2);
  near(context.sources[1].startedAt, context.sources[0].startedAt + 0.2);
});

test("ended fires only after audio_end and after the last source finished", () => {
  const { context, events, scheduler } = setup();
  scheduler.registerReply(2, "r-2");
  scheduler.enqueue(chunk(2, 0, 0.2));
  scheduler.enqueue(chunk(2, 1, 0.2));
  context.sources[0].finish();
  context.sources[1].finish();
  assert.deepEqual(events, ["started:r-2"], "no ended before audio_end");
  assert.equal(scheduler.isPlaying, false);

  scheduler.enqueue(chunk(2, 2, 0.2));
  scheduler.markAudioEnd(2, 3);
  assert.deepEqual(events, ["started:r-2"], "last chunk still playing");
  context.sources[2].finish();
  assert.deepEqual(events, ["started:r-2", "ended:r-2"]);

  scheduler.enqueue(chunk(2, 3, 0.2));
  assert.equal(context.sources.length, 3, "late chunks of a finished reply are dropped");
});

test("audio_end with a missing chunk skips the gap instead of stalling", () => {
  const { context, events, scheduler } = setup();
  scheduler.registerReply(4, "r-4");
  scheduler.enqueue(chunk(4, 0, 0.1));
  scheduler.enqueue(chunk(4, 2, 0.1));
  assert.equal(context.sources.length, 1);
  scheduler.markAudioEnd(4, 3);
  assert.equal(context.sources.length, 2);
  context.sources.forEach((source) => source.finish());
  assert.deepEqual(events, ["started:r-4", "ended:r-4"]);
});

test("stop halts every scheduled source immediately and drops later chunks", () => {
  const { context, events, scheduler } = setup();
  scheduler.registerReply(3, "r-3");
  scheduler.enqueue(chunk(3, 0, 1));
  scheduler.enqueue(chunk(3, 1, 1));
  scheduler.enqueue(chunk(3, 3, 1)); // pending (waiting for 2)
  context.currentTime = 0.4;
  const stopped = scheduler.stopAll();
  assert.deepEqual(stopped.map((event) => event.state), ["stopped"]);
  assert.ok(context.sources.every((source) => source.stopped));
  assert.deepEqual(events, ["started:r-3", "stopped:r-3"]);
  assert.equal(scheduler.isPlaying, false);
  assert.equal(scheduler.nextStartTime, 0);

  // Stopped sources reporting `ended` must not emit `ended`.
  context.sources.forEach((source) => source.finish());
  scheduler.enqueue(chunk(3, 2, 1));
  scheduler.markAudioEnd(3, 4);
  assert.deepEqual(events, ["started:r-3", "stopped:r-3"]);
  assert.equal(context.sources.length, 2);

  // The next reply starts fresh at now + lead-in.
  scheduler.registerReply(4, "r-4");
  scheduler.enqueue(chunk(4, 0, 0.5));
  near(context.sources[2].startedAt, 0.45);
  assert.deepEqual(events.at(-1), "started:r-4");
});

test("stop for a reply whose audio has not arrived yet still drops its chunks", () => {
  const { context, events, scheduler } = setup();
  scheduler.registerReply(9, "r-9");
  scheduler.stopAll("r-9");
  assert.deepEqual(events, ["stopped:r-9"]);
  scheduler.enqueue(chunk(9, 0, 0.5));
  assert.equal(context.sources.length, 0);
});

test("consecutive replies queue gaplessly; an underrun restarts at now + lead-in", () => {
  const { context, events, scheduler } = setup();
  scheduler.registerReply(1, "r-1");
  scheduler.registerReply(2, "r-2");
  scheduler.enqueue(chunk(1, 0, 0.5));
  scheduler.enqueue(chunk(2, 0, 0.5));
  near(context.sources[1].startedAt, 0.55);
  assert.deepEqual(events, ["started:r-1", "started:r-2"]);

  context.currentTime = 5; // network stall: queue ran dry
  scheduler.enqueue(chunk(2, 1, 0.5));
  near(context.sources[2].startedAt, 5.05);
  assert.equal(scheduler.bufferedSec > 0, true);
});

test("audio arriving before assistant_start is played and named once registered", () => {
  const { events, scheduler } = setup();
  scheduler.enqueue(chunk(11, 0, 0.2));
  scheduler.registerReply(11, "r-11");
  assert.equal(scheduler.seqForId("r-11"), 11);
  scheduler.stopAll();
  assert.deepEqual(events, ["started:11", "stopped:r-11"]);
});
