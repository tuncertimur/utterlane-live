// Gapless assistant-audio scheduler.
//
// Each UTA1 chunk becomes one AudioBufferSourceNode scheduled at a running
// `nextStartTime`, so consecutive chunks (and consecutive replies) play back to
// back without gaps. The context and destination are injected so the scheduler
// can be unit-tested with a fake clock (`context.currentTime`).

import { pcm16ToFloat32, type AudioChunk } from "./audio-header.ts";

export interface AudioBufferLike {
  readonly duration: number;
  getChannelData(channel: number): Float32Array;
}

export interface SourceNodeLike {
  buffer: AudioBufferLike | null;
  onended: (() => void) | null;
  connect(destination: unknown): unknown;
  start(when?: number): void;
  stop(when?: number): void;
  disconnect?(): void;
}

export interface PlaybackContextLike {
  readonly currentTime: number;
  createBuffer(channels: number, length: number, sampleRate: number): AudioBufferLike;
  createBufferSource(): SourceNodeLike;
}

export type PlaybackEventState = "started" | "ended" | "stopped";

export interface PlaybackEvent {
  state: PlaybackEventState;
  replySeq: number;
  replyId: string | null;
}

export interface PlaybackSchedulerOptions {
  context: PlaybackContextLike;
  /** Node every source connects to (e.g. a gain → analyser → destination chain). */
  destination: unknown;
  onEvent?: (event: PlaybackEvent) => void;
  /** Safety margin before the first chunk / after an underrun, in seconds. */
  leadInSec?: number;
}

interface ReplyState {
  seq: number;
  id: string | null;
  nextIndex: number;
  pending: Map<number, AudioChunk>;
  sources: Set<SourceNodeLike>;
  scheduled: number;
  ended: number;
  audioEnd: boolean;
  totalChunks: number | null;
  started: boolean;
  closed: boolean;
}

const CLOSED_MEMORY = 64;

export class PlaybackScheduler {
  private readonly context: PlaybackContextLike;
  private readonly destination: unknown;
  private readonly onEvent: (event: PlaybackEvent) => void;
  private readonly leadIn: number;
  private readonly replies = new Map<number, ReplyState>();
  private readonly idToSeq = new Map<string, number>();
  private readonly closedSeqs: number[] = [];
  private nextStart = 0;
  private underrunCount = 0;

  constructor(options: PlaybackSchedulerOptions) {
    this.context = options.context;
    this.destination = options.destination;
    this.onEvent = options.onEvent ?? (() => undefined);
    this.leadIn = options.leadInSec ?? 0.05;
  }

  /** Associate a reply id with its numeric sequence (from `assistant_start`). */
  registerReply(replySeq: number, replyId: string): void {
    this.idToSeq.set(replyId, replySeq);
    const state = this.replies.get(replySeq);
    if (state) state.id = replyId;
    else if (!this.isClosed(replySeq)) this.replies.set(replySeq, this.newReply(replySeq, replyId));
  }

  seqForId(replyId: string): number | null {
    return this.idToSeq.get(replyId) ?? null;
  }

  enqueue(chunk: AudioChunk): void {
    if (this.isClosed(chunk.replySeq)) return;
    let state = this.replies.get(chunk.replySeq);
    if (!state) {
      state = this.newReply(chunk.replySeq, null);
      this.replies.set(chunk.replySeq, state);
    }
    if (chunk.chunkIndex < state.nextIndex || state.pending.has(chunk.chunkIndex)) return; // duplicate
    state.pending.set(chunk.chunkIndex, chunk);
    this.flush(state);
  }

  /** `audio_end`: no more chunks will arrive for this reply. */
  markAudioEnd(replySeq: number, totalChunks: number): void {
    if (this.isClosed(replySeq)) return;
    let state = this.replies.get(replySeq);
    if (!state) {
      state = this.newReply(replySeq, null);
      this.replies.set(replySeq, state);
    }
    state.audioEnd = true;
    state.totalChunks = totalChunks;
    this.flush(state);
    this.checkFinished(state);
  }

  /**
   * Stop every scheduled source immediately. Emits `stopped` for every reply that
   * was still active. `alsoReplyId` marks a reply as stopped even if none of its
   * audio has arrived yet (late chunks are then dropped).
   */
  stopAll(alsoReplyId?: string | null): PlaybackEvent[] {
    const events: PlaybackEvent[] = [];
    if (alsoReplyId) {
      const seq = this.idToSeq.get(alsoReplyId);
      if (seq !== undefined && !this.replies.has(seq) && !this.isClosed(seq)) {
        this.replies.set(seq, this.newReply(seq, alsoReplyId));
      }
    }
    for (const state of [...this.replies.values()]) {
      for (const source of state.sources) {
        source.onended = null;
        try {
          source.stop();
        } catch {
          // Already stopped or never started.
        }
        try {
          source.disconnect?.();
        } catch {
          // Ignore.
        }
      }
      state.sources.clear();
      state.pending.clear();
      const event: PlaybackEvent = { state: "stopped", replySeq: state.seq, replyId: state.id };
      events.push(event);
      this.close(state);
    }
    this.nextStart = 0;
    for (const event of events) this.onEvent(event);
    return events;
  }

  /** True while any source is scheduled or playing. */
  get isPlaying(): boolean {
    for (const state of this.replies.values()) {
      if (state.sources.size > 0) return true;
    }
    return false;
  }

  get nextStartTime(): number {
    return this.nextStart;
  }

  get underruns(): number {
    return this.underrunCount;
  }

  /** Seconds of audio still queued ahead of the clock. */
  get bufferedSec(): number {
    return Math.max(0, this.nextStart - this.context.currentTime);
  }

  get activeReplySeqs(): number[] {
    return [...this.replies.keys()];
  }

  // -------------------------------------------------------------------------

  private newReply(seq: number, id: string | null): ReplyState {
    return {
      seq,
      id,
      nextIndex: 0,
      pending: new Map(),
      sources: new Set(),
      scheduled: 0,
      ended: 0,
      audioEnd: false,
      totalChunks: null,
      started: false,
      closed: false,
    };
  }

  private isClosed(seq: number): boolean {
    return this.closedSeqs.includes(seq);
  }

  private close(state: ReplyState): void {
    state.closed = true;
    this.replies.delete(state.seq);
    this.closedSeqs.push(state.seq);
    if (this.closedSeqs.length > CLOSED_MEMORY) this.closedSeqs.shift();
    if (state.id) this.idToSeq.delete(state.id);
  }

  private flush(state: ReplyState): void {
    for (;;) {
      let chunk = state.pending.get(state.nextIndex);
      if (!chunk && state.audioEnd && state.pending.size > 0) {
        // A chunk went missing and the server says the reply is complete:
        // skip the gap rather than stall forever.
        const nextAvailable = Math.min(...state.pending.keys());
        state.nextIndex = nextAvailable;
        chunk = state.pending.get(nextAvailable);
      }
      if (!chunk) return;
      state.pending.delete(state.nextIndex);
      state.nextIndex += 1;
      this.schedule(state, chunk);
    }
  }

  private schedule(state: ReplyState, chunk: AudioChunk): void {
    if (chunk.samples.length === 0) return;
    const buffer = this.context.createBuffer(1, chunk.samples.length, chunk.sampleRate);
    pcm16ToFloat32(chunk.samples, buffer.getChannelData(0));
    const source = this.context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.destination);

    const now = this.context.currentTime;
    let startAt = this.nextStart;
    if (startAt < now + 0.005) {
      if (this.nextStart > 0 && this.isPlaying) this.underrunCount += 1;
      startAt = now + this.leadIn;
    }
    this.nextStart = startAt + buffer.duration;

    state.sources.add(source);
    state.scheduled += 1;
    source.onended = () => {
      source.onended = null;
      state.sources.delete(source);
      try {
        source.disconnect?.();
      } catch {
        // Ignore.
      }
      if (state.closed) return;
      state.ended += 1;
      this.checkFinished(state);
    };
    source.start(startAt);

    if (!state.started) {
      state.started = true;
      this.onEvent({ state: "started", replySeq: state.seq, replyId: state.id });
    }
  }

  private checkFinished(state: ReplyState): void {
    if (state.closed || !state.audioEnd) return;
    if (state.pending.size > 0 || state.ended < state.scheduled) return;
    const started = state.started;
    this.close(state);
    if (started) this.onEvent({ state: "ended", replySeq: state.seq, replyId: state.id });
  }
}
