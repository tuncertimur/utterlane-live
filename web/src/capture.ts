// Microphone capture: getUserMedia → AudioContext (16 kHz when possible) →
// AudioWorklet (public/capture-worklet.js) → 640-byte PCM16 frames.
// Pure helpers are exported separately so they can be unit-tested in Node.

import { MIC_SAMPLE_RATE } from "./protocol.ts";

export type CaptureErrorKind = "denied" | "no_device" | "busy" | "insecure" | "unsupported" | "unknown";
export type MicPermission = "granted" | "denied" | "prompt" | "unknown";

export interface MicDevice {
  deviceId: string;
  /** Empty until microphone permission is granted; the view shows a localized fallback. */
  label: string;
}

export interface CaptureHandle {
  readonly sampleRate: number;
  readonly resampling: boolean;
  readonly deviceId: string | null;
  /** The track label; empty when the browser gives none. */
  readonly label: string;
  stop(): Promise<void>;
}

export interface StartCaptureOptions {
  deviceId: string | null;
  onFrame: (pcm: ArrayBuffer, rms: number) => void;
  /** Track ended unexpectedly (device unplugged, permission revoked). */
  onEnded?: () => void;
  workletUrl?: string;
}

export class CaptureError extends Error {
  readonly kind: CaptureErrorKind;
  constructor(kind: CaptureErrorKind, message?: string) {
    super(message ?? kind);
    this.kind = kind;
  }
}

export function buildAudioConstraints(deviceId: string | null): MediaTrackConstraints {
  const constraints: MediaTrackConstraints = {
    channelCount: { ideal: 1 },
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
  };
  if (deviceId) constraints.deviceId = { exact: deviceId };
  return constraints;
}

export function classifyMediaError(error: unknown): CaptureErrorKind {
  if (error instanceof CaptureError) return error.kind;
  const name = typeof error === "object" && error !== null && "name" in error ? String((error as { name: unknown }).name) : "";
  switch (name) {
    case "NotAllowedError":
    case "PermissionDeniedError":
    case "SecurityError":
      return "denied";
    case "NotFoundError":
    case "DevicesNotFoundError":
    case "OverconstrainedError":
    case "ConstraintNotSatisfiedError":
      return "no_device";
    case "NotReadableError":
    case "TrackStartError":
    case "AbortError":
      return "busy";
    case "TypeError":
      return "unsupported";
    default:
      return "unknown";
  }
}

/** Map linear RMS (0..1) to a perceptual 0..1 meter value over -60..-6 dBFS. */
export function rmsToMeter(rms: number): number {
  if (!(rms > 0)) return 0;
  const db = 20 * Math.log10(rms);
  const value = (db + 60) / 54;
  return value < 0 ? 0 : value > 1 ? 1 : value;
}

export function micSupport(env: { isSecureContext?: boolean; mediaDevices?: unknown; AudioWorkletNode?: unknown }): CaptureErrorKind | null {
  if (env.isSecureContext === false) return "insecure";
  if (!env.mediaDevices) return "unsupported";
  if (!env.AudioWorkletNode) return "unsupported";
  return null;
}

export async function queryMicPermission(): Promise<MicPermission> {
  try {
    if (typeof navigator === "undefined" || !navigator.permissions?.query) return "unknown";
    const status = await navigator.permissions.query({ name: "microphone" as PermissionName });
    return status.state === "granted" || status.state === "denied" || status.state === "prompt" ? status.state : "unknown";
  } catch {
    return "unknown";
  }
}

export async function listMicrophones(): Promise<MicDevice[]> {
  try {
    if (!navigator.mediaDevices?.enumerateDevices) return [];
    const devices = await navigator.mediaDevices.enumerateDevices();
    return devices
      .filter((device) => device.kind === "audioinput")
      .map((device) => ({ deviceId: device.deviceId, label: device.label }));
  } catch {
    return [];
  }
}

async function openStream(deviceId: string | null): Promise<MediaStream> {
  try {
    return await navigator.mediaDevices.getUserMedia({ audio: buildAudioConstraints(deviceId), video: false });
  } catch (error) {
    // A remembered device that is gone: fall back to the default microphone.
    if (deviceId && classifyMediaError(error) === "no_device") {
      return navigator.mediaDevices.getUserMedia({ audio: buildAudioConstraints(null), video: false });
    }
    throw error;
  }
}

function createContextAndSource(stream: MediaStream): { context: AudioContext; source: MediaStreamAudioSourceNode } {
  // Prefer a native 16 kHz graph (the browser's own resampler). Some browsers refuse
  // to connect a microphone to a context at a different rate; fall back to the
  // default rate and let the worklet do a band-limited resample.
  try {
    const context = new AudioContext({ sampleRate: MIC_SAMPLE_RATE, latencyHint: "interactive" });
    try {
      return { context, source: context.createMediaStreamSource(stream) };
    } catch {
      void context.close().catch(() => undefined);
    }
  } catch {
    // Fall through.
  }
  const context = new AudioContext({ latencyHint: "interactive" });
  return { context, source: context.createMediaStreamSource(stream) };
}

export async function startCapture(options: StartCaptureOptions): Promise<CaptureHandle> {
  const unsupported = micSupport({
    isSecureContext: typeof window !== "undefined" ? window.isSecureContext : undefined,
    mediaDevices: typeof navigator !== "undefined" ? navigator.mediaDevices : undefined,
    AudioWorkletNode: typeof AudioWorkletNode !== "undefined" ? AudioWorkletNode : undefined,
  });
  if (unsupported) throw new CaptureError(unsupported);

  const stream = await openStream(options.deviceId);
  const track = stream.getAudioTracks()[0];
  if (!track) {
    stream.getTracks().forEach((t) => t.stop());
    throw new CaptureError("no_device");
  }

  let context: AudioContext | null = null;
  try {
    const graph = createContextAndSource(stream);
    context = graph.context;
    const workletUrl = options.workletUrl ?? new URL("capture-worklet.js", document.baseURI).href;
    await context.audioWorklet.addModule(workletUrl);
    const node = new AudioWorkletNode(context, "utterlane-capture", {
      numberOfInputs: 1,
      numberOfOutputs: 1,
      outputChannelCount: [1],
      channelCount: 1,
      channelCountMode: "explicit",
      channelInterpretation: "speakers",
    });
    let resampling = Math.abs(context.sampleRate - MIC_SAMPLE_RATE) >= 1;
    node.port.onmessage = (event: MessageEvent) => {
      const data = event.data as { type?: string; pcm?: ArrayBuffer; rms?: number; resampling?: boolean };
      if (data?.type === "frame" && data.pcm) options.onFrame(data.pcm, data.rms ?? 0);
      else if (data?.type === "ready" && typeof data.resampling === "boolean") resampling = data.resampling;
    };
    // A silent sink keeps the worklet pulled by the graph in every browser.
    const sink = context.createGain();
    sink.gain.value = 0;
    graph.source.connect(node);
    node.connect(sink);
    sink.connect(context.destination);
    if (context.state === "suspended") await context.resume();

    let stopped = false;
    const onTrackEnded = () => {
      if (!stopped) options.onEnded?.();
    };
    track.addEventListener("ended", onTrackEnded);
    const settings = track.getSettings();
    const activeContext = context;

    return {
      sampleRate: activeContext.sampleRate,
      get resampling() {
        return resampling;
      },
      deviceId: settings.deviceId ?? options.deviceId,
      label: track.label,
      async stop() {
        if (stopped) return;
        stopped = true;
        track.removeEventListener("ended", onTrackEnded);
        node.port.onmessage = null;
        try {
          graph.source.disconnect();
          node.disconnect();
          sink.disconnect();
        } catch {
          // Ignore.
        }
        stream.getTracks().forEach((t) => t.stop());
        await activeContext.close().catch(() => undefined);
      },
    };
  } catch (error) {
    stream.getTracks().forEach((t) => t.stop());
    if (context) void context.close().catch(() => undefined);
    throw error;
  }
}
