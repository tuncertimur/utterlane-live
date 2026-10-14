import assert from "node:assert/strict";
import test from "node:test";
import { LiveConnection, backoffDelay, liveSocketUrl } from "./connection.ts";

class FakeTimers {
  now = 0;
  handles = new Map();
  next = 1;
  setTimeout = (fn, ms) => this.add(fn, ms, false);
  setInterval = (fn, ms) => this.add(fn, ms, true);
  clearTimeout = (handle) => this.handles.delete(handle);
  clearInterval = (handle) => this.handles.delete(handle);
  add(fn, ms, repeat) {
    const id = this.next++;
    this.handles.set(id, { fn, at: this.now + ms, ms, repeat });
    return id;
  }
  advance(ms) {
    const end = this.now + ms;
    for (;;) {
      let due = null;
      for (const [id, timer] of this.handles) {
        if (timer.at <= end && (!due || timer.at < due[1].at)) due = [id, timer];
      }
      if (!due) break;
      const [id, timer] = due;
      this.now = timer.at;
      if (timer.repeat) timer.at += timer.ms;
      else this.handles.delete(id);
      timer.fn();
    }
    this.now = end;
  }
}

class FakeSocket {
  static all = [];
  readyState = 0;
  binaryType = "blob";
  bufferedAmount = 0;
  sent = [];
  closed = null;
  onopen = null;
  onclose = null;
  onerror = null;
  onmessage = null;
  constructor(url) {
    this.url = url;
    FakeSocket.all.push(this);
  }
  send(data) {
    this.sent.push(data);
  }
  close(code, reason) {
    this.closed = { code, reason };
    this.readyState = 3;
  }
  // Test helpers
  open() {
    this.readyState = 1;
    this.onopen?.({});
  }
  receive(data) {
    this.onmessage?.({ data });
  }
  drop() {
    this.readyState = 3;
    this.onclose?.({});
  }
  json() {
    return this.sent.filter((item) => typeof item === "string").map((item) => JSON.parse(item));
  }
}

function setup(overrides = {}) {
  FakeSocket.all = [];
  const timers = new FakeTimers();
  const log = { messages: [], binary: [], states: [], invalid: [] };
  const connection = new LiveConnection({
    url: "ws://127.0.0.1:8765/ws/live",
    createSocket: (url) => new FakeSocket(url),
    onMessage: (message) => log.messages.push(message),
    onBinary: (data) => log.binary.push(data),
    onState: (state, retry) => log.states.push(retry === null ? state : `${state}:${retry}`),
    onInvalid: (error) => log.invalid.push(error),
    timers,
    now: () => timers.now,
    random: () => 0.5,
    ...overrides,
  });
  return { connection, timers, log };
}

test("socket URL follows the page origin", () => {
  assert.equal(liveSocketUrl({ protocol: "http:", host: "127.0.0.1:8765" }), "ws://127.0.0.1:8765/ws/live");
  assert.equal(liveSocketUrl({ protocol: "https:", host: "live.local" }), "wss://live.local/ws/live");
});

test("backoff grows exponentially, is capped and jittered", () => {
  assert.equal(backoffDelay(0, () => 0.5), 500);
  assert.equal(backoffDelay(1, () => 0.5), 1000);
  assert.equal(backoffDelay(3, () => 0.5), 4000);
  assert.equal(backoffDelay(50, () => 0.5), 10000);
  assert.equal(backoffDelay(0, () => 0), 400);
  assert.equal(backoffDelay(0, () => 1), 600);
});

test("sends hello on open and pings every 15 seconds", () => {
  const { connection, timers, log } = setup();
  connection.start();
  const socket = FakeSocket.all[0];
  assert.equal(socket.binaryType, "arraybuffer");
  socket.open();
  assert.deepEqual(socket.json()[0], { type: "hello", client: "utterlane-live-web", version: 1, sample_rate: 16000 });
  assert.deepEqual(log.states, ["connecting", "open"]);

  socket.receive('{"type":"pong","t":1}'); // keep the watchdog happy
  timers.advance(15_000);
  assert.deepEqual(socket.json()[1], { type: "ping", t: 15_000 });
  socket.receive('{"type":"pong","t":15000}');
  timers.advance(15_000);
  assert.equal(socket.json().filter((message) => message.type === "ping").length, 2);
});

test("unknown and invalid messages are ignored without closing the socket", () => {
  const { connection, log } = setup();
  connection.start();
  const socket = FakeSocket.all[0];
  socket.open();
  socket.receive('{"type":"from_the_future","x":1}');
  socket.receive("not json at all");
  socket.receive('{"type":"turn","turn":{}}');
  socket.receive('{"type":"status","state":"listening"}');
  socket.receive(new ArrayBuffer(20));
  socket.receive(new Uint8Array([1, 2, 3]));
  assert.equal(socket.closed, null);
  assert.equal(log.messages.length, 1);
  assert.equal(log.messages[0].type, "status");
  assert.equal(log.invalid.length, 2);
  assert.equal(log.binary.length, 2);
  assert.equal(log.binary[1].byteLength, 3);
});

test("a throwing message handler never tears down the socket", () => {
  const { connection, log } = setup({
    onMessage: () => {
      throw new Error("render bug");
    },
  });
  connection.start();
  const socket = FakeSocket.all[0];
  socket.open();
  socket.receive('{"type":"status","state":"thinking"}');
  assert.equal(socket.closed, null);
  assert.deepEqual(log.invalid, ["render bug"]);
});

test("reconnects with backoff after a drop and resets the attempt counter on success", () => {
  const { connection, timers, log } = setup();
  connection.start();
  FakeSocket.all[0].open();
  FakeSocket.all[0].drop();
  assert.equal(log.states.at(-1), "reconnecting:500");
  timers.advance(499);
  assert.equal(FakeSocket.all.length, 1);
  timers.advance(1);
  assert.equal(FakeSocket.all.length, 2);
  FakeSocket.all[1].drop(); // failed attempt
  assert.equal(log.states.at(-1), "reconnecting:1000");
  timers.advance(1000);
  const third = FakeSocket.all[2];
  third.open();
  assert.equal(third.json()[0].type, "hello", "hello is sent again after reconnect");
  third.drop();
  assert.equal(log.states.at(-1), "reconnecting:500");
});

test("retryNow skips the wait; stop cancels reconnects", () => {
  const { connection, timers, log } = setup();
  connection.start();
  FakeSocket.all[0].drop();
  connection.retryNow();
  assert.equal(FakeSocket.all.length, 2);
  connection.stop();
  assert.equal(log.states.at(-1), "closed");
  timers.advance(60_000);
  assert.equal(FakeSocket.all.length, 2);
});

test("a silent half-open socket is replaced by the watchdog", () => {
  const { connection, timers } = setup({ staleAfterMs: 20_000 });
  connection.start();
  const socket = FakeSocket.all[0];
  socket.open();
  timers.advance(30_000);
  assert.equal(socket.closed?.reason, "stale");
  timers.advance(1_000);
  assert.equal(FakeSocket.all.length, 2);
});

test("binary sends are dropped under backpressure and when closed", () => {
  const { connection } = setup();
  const frame = new ArrayBuffer(640);
  assert.equal(connection.sendBinary(frame), false);
  connection.start();
  const socket = FakeSocket.all[0];
  socket.open();
  assert.equal(connection.sendBinary(frame), true);
  socket.bufferedAmount = 1_000_000;
  assert.equal(connection.sendBinary(frame), false);
  assert.equal(connection.dropped, 1);
  assert.equal(socket.sent.filter((item) => item instanceof ArrayBuffer).length, 1);
});
