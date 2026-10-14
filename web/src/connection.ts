// WebSocket connection to /ws/live with auto-reconnect, hello, keep-alive pings
// and a stale-socket watchdog. Unknown or invalid messages are dropped quietly;
// they never close the socket.

import {
  encodeClientMessage,
  helloMessage,
  parseServerMessage,
  type ClientMessage,
  type ServerMessage,
} from "./protocol.ts";
import type { ConnectionState } from "./store.ts";

export interface SocketLike {
  readonly readyState: number;
  binaryType: string;
  readonly bufferedAmount: number;
  onopen: ((event: unknown) => void) | null;
  onclose: ((event: unknown) => void) | null;
  onerror: ((event: unknown) => void) | null;
  onmessage: ((event: { data: unknown }) => void) | null;
  send(data: string | ArrayBuffer | ArrayBufferView): void;
  close(code?: number, reason?: string): void;
}

export interface Timers {
  setTimeout(fn: () => void, ms: number): unknown;
  clearTimeout(handle: unknown): void;
  setInterval(fn: () => void, ms: number): unknown;
  clearInterval(handle: unknown): void;
}

export interface LiveConnectionOptions {
  url: string;
  createSocket?: (url: string) => SocketLike;
  onMessage: (message: ServerMessage) => void;
  onBinary: (data: ArrayBuffer) => void;
  onState: (state: ConnectionState, retryInMs: number | null) => void;
  onInvalid?: (error: string) => void;
  timers?: Timers;
  now?: () => number;
  random?: () => number;
  pingIntervalMs?: number;
  staleAfterMs?: number;
  /** Drop outgoing audio frames when this many bytes are already queued. */
  maxBufferedBytes?: number;
}

const OPEN = 1;

export function liveSocketUrl(location: { protocol: string; host: string }): string {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${location.host}/ws/live`;
}

/** Exponential backoff: 0.5 s, 1 s, 2 s … capped at 10 s, with ±20 % jitter. */
export function backoffDelay(attempt: number, random: () => number = Math.random): number {
  const base = Math.min(10_000, 500 * 2 ** Math.max(0, Math.min(attempt, 10)));
  const jitter = 1 + (random() * 0.4 - 0.2);
  return Math.round(base * jitter);
}

const defaultTimers: Timers = {
  setTimeout: (fn, ms) => globalThis.setTimeout(fn, ms),
  clearTimeout: (handle) => globalThis.clearTimeout(handle as ReturnType<typeof setTimeout>),
  setInterval: (fn, ms) => globalThis.setInterval(fn, ms),
  clearInterval: (handle) => globalThis.clearInterval(handle as ReturnType<typeof setInterval>),
};

export class LiveConnection {
  private readonly options: LiveConnectionOptions;
  private readonly timers: Timers;
  private readonly now: () => number;
  private socket: SocketLike | null = null;
  private attempt = 0;
  private retryHandle: unknown = null;
  private pingHandle: unknown = null;
  private lastMessageAt = 0;
  private running = false;
  private droppedFrames = 0;

  constructor(options: LiveConnectionOptions) {
    this.options = options;
    this.timers = options.timers ?? defaultTimers;
    this.now = options.now ?? (() => Date.now());
  }

  start(): void {
    if (this.running) return;
    this.running = true;
    this.connect();
  }

  stop(): void {
    this.running = false;
    this.clearRetry();
    this.clearPing();
    const socket = this.socket;
    this.socket = null;
    if (socket) {
      this.detach(socket);
      try {
        socket.close(1000, "client_stop");
      } catch {
        // Ignore.
      }
    }
    this.options.onState("closed", null);
  }

  /** Skip the remaining backoff and reconnect immediately. */
  retryNow(): void {
    if (!this.running || this.isOpen) return;
    this.clearRetry();
    this.attempt = 0;
    this.connect();
  }

  get isOpen(): boolean {
    return this.socket !== null && this.socket.readyState === OPEN;
  }

  get dropped(): number {
    return this.droppedFrames;
  }

  send(message: ClientMessage): boolean {
    if (!this.isOpen || !this.socket) return false;
    try {
      this.socket.send(encodeClientMessage(message));
      return true;
    } catch {
      return false;
    }
  }

  sendBinary(data: ArrayBuffer): boolean {
    if (!this.isOpen || !this.socket) return false;
    const limit = this.options.maxBufferedBytes ?? 64 * 1024;
    if (this.socket.bufferedAmount > limit) {
      this.droppedFrames += 1;
      return false;
    }
    try {
      this.socket.send(data);
      return true;
    } catch {
      return false;
    }
  }

  // -------------------------------------------------------------------------

  private connect(): void {
    if (!this.running) return;
    this.options.onState(this.attempt === 0 ? "connecting" : "reconnecting", null);
    let socket: SocketLike;
    try {
      socket = this.options.createSocket
        ? this.options.createSocket(this.options.url)
        : (new WebSocket(this.options.url) as unknown as SocketLike);
    } catch {
      this.scheduleReconnect();
      return;
    }
    socket.binaryType = "arraybuffer";
    this.socket = socket;

    socket.onopen = () => {
      if (this.socket !== socket) return;
      this.attempt = 0;
      this.lastMessageAt = this.now();
      this.options.onState("open", null);
      this.send(helloMessage());
      this.startPing();
    };
    socket.onmessage = (event) => {
      if (this.socket !== socket) return;
      this.lastMessageAt = this.now();
      this.handleData(event.data);
    };
    socket.onerror = () => {
      // `close` follows; reconnect is handled there.
    };
    socket.onclose = () => {
      if (this.socket !== socket) return;
      this.detach(socket);
      this.socket = null;
      this.clearPing();
      this.scheduleReconnect();
    };
  }

  private handleData(data: unknown): void {
    try {
      if (typeof data === "string") {
        const result = parseServerMessage(data);
        if (!result.ok) {
          this.options.onInvalid?.(result.error);
          return;
        }
        if (result.message.type === "unknown") return;
        this.options.onMessage(result.message);
        return;
      }
      if (data instanceof ArrayBuffer) {
        this.options.onBinary(data);
        return;
      }
      if (ArrayBuffer.isView(data)) {
        const view = data as ArrayBufferView;
        const copy = new Uint8Array(view.byteLength);
        copy.set(new Uint8Array(view.buffer, view.byteOffset, view.byteLength));
        this.options.onBinary(copy.buffer);
        return;
      }
      if (typeof Blob !== "undefined" && data instanceof Blob) {
        void data.arrayBuffer().then(
          (buffer) => this.options.onBinary(buffer),
          () => undefined,
        );
      }
    } catch (error) {
      // A rendering bug must never tear down the socket.
      this.options.onInvalid?.(error instanceof Error ? error.message : "handler_error");
    }
  }

  private scheduleReconnect(): void {
    if (!this.running) return;
    const delay = backoffDelay(this.attempt, this.options.random);
    this.attempt += 1;
    this.options.onState("reconnecting", delay);
    this.clearRetry();
    this.retryHandle = this.timers.setTimeout(() => {
      this.retryHandle = null;
      this.connect();
    }, delay);
  }

  private startPing(): void {
    this.clearPing();
    const interval = this.options.pingIntervalMs ?? 15_000;
    const staleAfter = this.options.staleAfterMs ?? 45_000;
    this.pingHandle = this.timers.setInterval(() => {
      const socket = this.socket;
      if (!socket) return;
      if (this.now() - this.lastMessageAt > staleAfter) {
        // Half-open socket: force a reconnect.
        this.detach(socket);
        this.socket = null;
        try {
          socket.close(4000, "stale");
        } catch {
          // Ignore.
        }
        this.clearPing();
        this.scheduleReconnect();
        return;
      }
      this.send({ type: "ping", t: this.now() });
    }, interval);
  }

  private clearPing(): void {
    if (this.pingHandle !== null) this.timers.clearInterval(this.pingHandle);
    this.pingHandle = null;
  }

  private clearRetry(): void {
    if (this.retryHandle !== null) this.timers.clearTimeout(this.retryHandle);
    this.retryHandle = null;
  }

  private detach(socket: SocketLike): void {
    socket.onopen = null;
    socket.onmessage = null;
    socket.onerror = null;
    socket.onclose = null;
  }
}
