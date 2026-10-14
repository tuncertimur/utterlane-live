// LiveEngine: the side-effectful glue between the WebSocket, the playback
// scheduler, microphone capture and the React store. It lives outside React so
// callbacks never see stale state.

import { decodeAudioChunk } from "./audio-header.ts";
import {
  CaptureError,
  classifyMediaError,
  listMicrophones,
  rmsToMeter,
  startCapture,
  type CaptureErrorKind,
  type CaptureHandle,
  type MicDevice,
} from "./capture.ts";
import { LiveConnection, liveSocketUrl } from "./connection.ts";
import { LevelBus, smoothLevel } from "./levels.ts";
import { PlaybackScheduler, type PlaybackContextLike, type PlaybackEvent } from "./playback.ts";
import { prefs } from "./prefs.ts";
import type { Mode, ServerMessage } from "./protocol.ts";
import type { LiveAction } from "./store.ts";

export type AudioPhase =
  | { phase: "idle" }
  | { phase: "starting" }
  | { phase: "running"; micLabel: string; sampleRate: number; resampling: boolean }
  | { phase: "listen_only" }
  | { phase: "error"; kind: CaptureErrorKind };

export interface EngineCallbacks {
  dispatch: (action: LiveAction) => void;
  onAudioPhase: (phase: AudioPhase) => void;
  onDevices: (devices: MicDevice[], selected: string | null) => void;
}

interface Player {
  context: AudioContext;
  scheduler: PlaybackScheduler;
  analyser: AnalyserNode;
  samples: Float32Array<ArrayBuffer>;
}

export class LiveEngine {
  readonly micLevel = new LevelBus();
  readonly speakLevel = new LevelBus();

  private readonly cb: EngineCallbacks;
  private connection: LiveConnection | null = null;
  private player: Player | null = null;
  private capture: CaptureHandle | null = null;
  private phase: AudioPhase = { phase: "idle" };
  private listening = true;
  private userMuted = false;
  private deviceId: string | null = prefs.micDeviceId();
  private readonly stoppedSent = new Set<string>();
  private readonly activeReplies = new Set<string>();
  private lastReplyId: string | null = null;
  private raf = 0;
  private micSmooth = 0;
  private speakSmooth = 0;
  private restarting = false;

  constructor(callbacks: EngineCallbacks) {
    this.cb = callbacks;
  }

  // -------------------------------------------------------------------------
  // Connection

  connect(): void {
    if (this.connection) return;
    this.connection = new LiveConnection({
      url: liveSocketUrl(window.location),
      onMessage: (message) => this.handleMessage(message),
      onBinary: (data) => this.handleAudio(data),
      onState: (state, retryInMs) => {
        this.cb.dispatch({ type: "connection", state, retryInMs, at: Date.now() });
        if (state === "reconnecting" && this.player?.scheduler.isPlaying) this.player.scheduler.stopAll();
      },
    });
    this.connection.start();
  }

  disconnect(): void {
    this.connection?.stop();
    this.connection = null;
  }

  retryNow(): void {
    this.connection?.retryNow();
  }

  /**
   * The server keeps one session; a newer tab replaced this one. Reconnecting on our
   * own would steal it back (and the other tab would do the same), so wait for the user.
   */
  private yieldToOtherTab(): void {
    this.player?.scheduler.stopAll();
    this.disconnect();
    this.cb.dispatch({ type: "connection", state: "replaced", at: Date.now() });
  }

  /** "Continue here": take the session back from the other tab. */
  takeOver(): void {
    this.connect();
  }

  private send(message: Parameters<LiveConnection["send"]>[0]): boolean {
    return this.connection?.send(message) ?? false;
  }

  private handleMessage(message: ServerMessage): void {
    this.cb.dispatch({ type: "server", message, at: Date.now() });
    switch (message.type) {
      case "ready":
        this.listening = message.listening;
        if (this.userMuted && message.listening) this.setListening(false);
        break;
      case "status":
        if (message.state === "paused") this.listening = false;
        else if (message.state === "listening") this.listening = !this.userMuted;
        break;
      case "assistant_start":
        this.lastReplyId = message.reply_id;
        this.activeReplies.add(message.reply_id);
        if (this.player) this.player.scheduler.registerReply(message.reply_seq, message.reply_id);
        else this.sendStopped(message.reply_id); // Audio not started yet: tell the server nothing will play.
        break;
      case "audio_end":
        this.player?.scheduler.markAudioEnd(message.reply_seq, message.chunks);
        break;
      case "assistant_end":
        this.activeReplies.delete(message.reply_id);
        break;
      case "stop_playback":
        this.stopPlayback(message.reply_id);
        break;
      case "notice":
        if (message.code === "replaced") this.yieldToOtherTab();
        break;
      default:
        break;
    }
  }

  private handleAudio(data: ArrayBuffer): void {
    const player = this.player;
    if (!player) return;
    const decoded = decodeAudioChunk(data);
    if (!decoded.ok) return;
    if (player.context.state === "suspended") void player.context.resume().catch(() => undefined);
    player.scheduler.enqueue(decoded.chunk);
  }

  // -------------------------------------------------------------------------
  // Playback

  private ensurePlayer(): Player {
    if (this.player) return this.player;
    const context = new AudioContext({ latencyHint: "interactive" });
    const gain = context.createGain();
    const analyser = context.createAnalyser();
    analyser.fftSize = 1024;
    analyser.smoothingTimeConstant = 0.2;
    gain.connect(analyser);
    analyser.connect(context.destination);
    const scheduler = new PlaybackScheduler({
      context: context as unknown as PlaybackContextLike,
      destination: gain,
      onEvent: (event) => this.handlePlaybackEvent(event),
    });
    void context.resume().catch(() => undefined);
    this.player = { context, scheduler, analyser, samples: new Float32Array(analyser.fftSize) };
    this.startMeterLoop();
    return this.player;
  }

  private handlePlaybackEvent(event: PlaybackEvent): void {
    const replyId = event.replyId;
    if (!replyId) return;
    if (event.state === "stopped") {
      this.sendStopped(replyId);
    } else {
      this.send({ type: "playback", state: event.state, reply_id: replyId });
    }
    this.cb.dispatch({ type: "local_playback", replyId, state: event.state, at: Date.now() });
  }

  private sendStopped(replyId: string): void {
    if (this.stoppedSent.has(replyId)) return;
    this.stoppedSent.add(replyId);
    if (this.stoppedSent.size > 200) {
      const first = this.stoppedSent.values().next().value;
      if (first !== undefined) this.stoppedSent.delete(first);
    }
    this.send({ type: "playback", state: "stopped", reply_id: replyId });
  }

  private stopPlayback(replyId: string | null): void {
    const player = this.player;
    if (player) player.scheduler.stopAll(replyId);
    if (replyId) {
      this.sendStopped(replyId);
      this.cb.dispatch({ type: "local_playback", replyId, state: "stopped", at: Date.now() });
    }
  }

  /** User pressed Stop (or Escape). */
  stopSpeaking(): void {
    const target = this.lastReplyId && this.activeReplies.has(this.lastReplyId) ? this.lastReplyId : null;
    const events = this.player?.scheduler.stopAll(target) ?? [];
    if (events.length === 0 && target) this.stopPlayback(target);
  }

  get isSpeaking(): boolean {
    return this.player?.scheduler.isPlaying ?? false;
  }

  private startMeterLoop(): void {
    if (this.raf || typeof requestAnimationFrame === "undefined") return;
    const tick = () => {
      this.raf = requestAnimationFrame(tick);
      const player = this.player;
      let target = 0;
      if (player && player.scheduler.isPlaying) {
        player.analyser.getFloatTimeDomainData(player.samples);
        let sum = 0;
        for (let i = 0; i < player.samples.length; i += 1) sum += player.samples[i] * player.samples[i];
        target = rmsToMeter(Math.sqrt(sum / player.samples.length));
      }
      this.speakSmooth = smoothLevel(this.speakSmooth, target, 0.5, 0.12);
      this.speakLevel.set(this.speakSmooth < 0.01 ? 0 : this.speakSmooth);
    };
    this.raf = requestAnimationFrame(tick);
  }

  // -------------------------------------------------------------------------
  // Capture

  private setPhase(phase: AudioPhase): void {
    this.phase = phase;
    this.cb.onAudioPhase(phase);
  }

  /** Must be called from a user gesture (click): creates the audio contexts. */
  async start(): Promise<void> {
    if (this.phase.phase === "starting" || this.phase.phase === "running") return;
    this.ensurePlayer(); // synchronously, inside the gesture
    this.setPhase({ phase: "starting" });
    try {
      await this.openCapture();
      void this.refreshDevices();
    } catch (error) {
      this.setPhase({ phase: "error", kind: classifyMediaError(error) });
    }
  }

  /** Continue without a microphone: assistant audio and the timeline still work. */
  startListenOnly(): void {
    this.ensurePlayer();
    this.setPhase({ phase: "listen_only" });
  }

  private async openCapture(): Promise<void> {
    const handle = await startCapture({
      deviceId: this.deviceId,
      onFrame: (pcm, rms) => this.handleFrame(pcm, rms),
      onEnded: () => void this.handleTrackEnded(),
    });
    this.capture = handle;
    this.setPhase({ phase: "running", micLabel: handle.label, sampleRate: handle.sampleRate, resampling: handle.resampling });
  }

  private handleFrame(pcm: ArrayBuffer, rms: number): void {
    const active = this.listening && !this.userMuted;
    this.micSmooth = smoothLevel(this.micSmooth, active ? rmsToMeter(rms) : 0, 0.55, 0.2);
    this.micLevel.set(this.micSmooth);
    if (active) this.connection?.sendBinary(pcm);
  }

  private async handleTrackEnded(): Promise<void> {
    if (this.restarting) return;
    this.cb.dispatch({ type: "toast", level: "warn", code: "mic_lost", key: "toast.micLost", at: Date.now() });
    this.deviceId = null;
    await this.restartCapture();
  }

  private async restartCapture(): Promise<void> {
    if (this.phase.phase !== "running" && this.phase.phase !== "error") return;
    this.restarting = true;
    try {
      await this.capture?.stop();
      this.capture = null;
      await this.openCapture();
    } catch (error) {
      this.setPhase({ phase: "error", kind: error instanceof CaptureError ? error.kind : classifyMediaError(error) });
    } finally {
      this.restarting = false;
    }
  }

  async refreshDevices(): Promise<void> {
    const devices = await listMicrophones();
    this.cb.onDevices(devices, this.deviceId);
  }

  async setDevice(deviceId: string | null): Promise<void> {
    this.deviceId = deviceId;
    prefs.setMicDeviceId(deviceId);
    this.cb.onDevices(await listMicrophones(), deviceId);
    if (this.phase.phase === "running") await this.restartCapture();
  }

  // -------------------------------------------------------------------------
  // Commands

  setListening(enabled: boolean): void {
    this.userMuted = !enabled;
    this.listening = enabled;
    if (!enabled) this.micLevel.set(0);
    this.cb.dispatch({ type: "set_listening", enabled });
    this.send({ type: "listen", enabled });
  }

  setMode(mode: Mode): void {
    this.cb.dispatch({ type: "set_mode", mode });
    this.send({ type: "set_mode", mode });
  }

  renameSpeaker(speakerId: string, name: string): void {
    this.send({ type: "rename_speaker", speaker_id: speakerId, name: name.trim() });
  }

  forgetSpeaker(speakerId: string): void {
    this.send({ type: "forget_speaker", speaker_id: speakerId });
  }

  resetSession(): void {
    this.stopSpeaking();
    if (this.send({ type: "reset_session" })) this.cb.dispatch({ type: "reset_local" });
  }

  resumeAudio(): void {
    if (this.player?.context.state === "suspended") void this.player.context.resume().catch(() => undefined);
  }

  dispose(): void {
    this.disconnect();
    if (this.raf && typeof cancelAnimationFrame !== "undefined") cancelAnimationFrame(this.raf);
    this.raf = 0;
    void this.capture?.stop();
    this.capture = null;
    if (this.player) void this.player.context.close().catch(() => undefined);
    this.player = null;
  }
}
