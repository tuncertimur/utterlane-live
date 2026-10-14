// Utterlane Live wire protocol (v1): types plus a validating, tolerant parser.
// See docs/PROTOCOL.md. Unknown fields are ignored, unknown message types are
// returned as `{ type: "unknown" }`, unknown enum values are preserved as strings so
// a newer backend never crashes an older UI.
//
// The parser never invents user-facing text: labels the server left out stay empty
// and the views fill them in, localized (see labels.ts `speakerLabel`).

export const PROTOCOL_VERSION = 1;
export const MIC_SAMPLE_RATE = 16000;
export const MIC_FRAME_SAMPLES = 320;
export const MIC_FRAME_BYTES = MIC_FRAME_SAMPLES * 2;
export const CLIENT_NAME = "utterlane-live-web";
export const UNKNOWN_SPEAKER_ID = "unknown";
export const SPEAKER_PALETTE_SIZE = 8;

export type Mode = "smart" | "always" | "wake";
export const MODES: readonly Mode[] = ["smart", "always", "wake"];

/** The server's conversation language (`utterlane --language`, `UTTERLANE_LANGUAGE`). */
export type Language = "en" | "tr";

export function isLanguage(value: unknown): value is Language {
  return value === "en" || value === "tr";
}

export type StatusState = "loading" | "listening" | "hearing" | "thinking" | "speaking" | "paused";
export const STATUS_STATES: readonly StatusState[] = [
  "loading",
  "listening",
  "hearing",
  "thinking",
  "speaking",
  "paused",
];

/** `off`: deliberately disabled (no AI brain configured, or speech output turned off). */
export type ModelStatus = "ready" | "loading" | "error" | "mock" | "off";
export interface ModelInfo {
  id: string;
  status: ModelStatus | (string & {});
  detail: string | null;
}
export type ModelMap = Record<string, ModelInfo>;

export interface Budget {
  spent_usd: number;
  cap_usd: number;
  calls: number;
}

export type NameState = "none" | "provisional" | "confirmed";

export interface Speaker {
  id: string;
  label: string;
  name: string | null;
  name_state: NameState;
  ordinal: number | null;
  persistent: boolean;
  present: boolean;
  turns: number;
  /** Palette index 0..7, or -1 for "no colour" (the ambiguous pseudo-speaker). */
  color: number;
}

export type AttributionState = "known" | "new" | "ambiguous" | "short" | "overlap";
export interface Attribution {
  state: AttributionState | (string & {});
  score: number | null;
  margin: number | null;
}

export type TurnFlag =
  | "self_intro"
  | "nomination"
  | "correction"
  | "consent_yes"
  | "consent_no"
  | "addressed_assistant"
  | "barge_in";

export interface Turn {
  id: string;
  speaker: Speaker;
  text: string;
  started_at: number | null;
  ended_at: number | null;
  duration_s: number | null;
  stt_ms: number | null;
  attribution: Attribution;
  flags: string[];
}

/** Proposed (optional) extension: assistant replies included in `history`. */
export interface HistoryReply {
  reply_id: string;
  in_reply_to: string | null;
  text: string;
  interrupted: boolean;
}

export type IdentityKind =
  | "new_speaker"
  | "named"
  | "provisional"
  | "confirmed"
  | "corrected"
  | "merged"
  | "profile_saved"
  | "consent_declined"
  | "profile_recognized"
  | "forgotten";

export interface Usage {
  input_tokens: number | null;
  output_tokens: number | null;
  cost_usd: number | null;
}

export type NoticeLevel = "info" | "warn" | "error";

/**
 * `assistant_silent.reason` values the UI knows. `no_brain`: listen-only mode, no AI
 * brain is configured, so the assistant never generates replies.
 */
export type SilentReason = "not_addressed" | "wake_word_missing" | "listening_off" | "speaker_not_allowed" | "no_brain";

export type ServerMessage =
  | {
      type: "ready";
      session_id: string;
      assistant_name: string;
      /** Null when an older backend does not send it. */
      language: Language | null;
      mode: Mode;
      listening: boolean;
      models: ModelMap;
      budget: Budget;
    }
  | { type: "status"; state: StatusState | (string & {}); detail: string | null }
  | { type: "level"; rms: number; speech: boolean }
  | { type: "history"; turns: Turn[]; replies: HistoryReply[] }
  | { type: "turn"; turn: Turn }
  | { type: "turn_update"; turn_id: string; speaker: Speaker }
  | { type: "speakers"; speakers: Speaker[] }
  | {
      type: "identity_event";
      kind: IdentityKind | (string & {});
      speaker_id: string | null;
      name: string | null;
      message: string;
    }
  | { type: "assistant_start"; reply_id: string; reply_seq: number; in_reply_to: string | null }
  | { type: "assistant_delta"; reply_id: string; text: string }
  | { type: "audio_end"; reply_id: string; reply_seq: number; chunks: number }
  | { type: "assistant_end"; reply_id: string; text: string; interrupted: boolean; usage: Usage | null }
  | { type: "assistant_silent"; in_reply_to: string | null; reason: SilentReason | (string & {}) }
  | { type: "stop_playback"; reply_id: string | null; reason: string }
  | { type: "usage"; spent_usd: number; cap_usd: number; calls: number }
  | { type: "notice"; level: NoticeLevel; code: string; message: string }
  | { type: "pong"; t: number | null }
  | { type: "unknown"; original_type: string };

export type ParseResult = { ok: true; message: ServerMessage } | { ok: false; error: string };

export type ClientMessage =
  | { type: "hello"; client: string; version: number; sample_rate: number }
  | { type: "playback"; state: "started" | "ended" | "stopped"; reply_id: string }
  | { type: "set_mode"; mode: Mode }
  | { type: "listen"; enabled: boolean }
  | { type: "rename_speaker"; speaker_id: string; name: string }
  | { type: "forget_speaker"; speaker_id: string }
  | { type: "reset_session" }
  | { type: "ping"; t: number };

export interface Profile {
  id: string;
  /** Empty when the server sent no name (the view shows a localized placeholder). */
  name: string;
  /** Epoch milliseconds, or null when the server value could not be read. */
  created_at: number | null;
  updated_at: number | null;
  samples: number;
}

/** `GET /api/health`. While the backend is still loading only status/version/models arrive. */
export interface Health {
  status: "ok" | "degraded" | "loading" | (string & {});
  version: string | null;
  language: Language | null;
  models: ModelMap;
  /** Human-readable hardware summary from the server (not localized). */
  hardware: string | null;
  gpu: { name: string | null; available: boolean } | null;
  /** Where the voice-profile key lives: `os_credential_store` or `file`. */
  profiles_key_storage: string | null;
}

/** AI brain kinds (`UTTERLANE_BRAIN`). Unknown values are passed through as strings. */
export type BrainKind =
  | "openai"
  | "openclaw"
  | "hermes"
  | "ollama"
  | "lmstudio"
  | "chat"
  | "responses"
  | "webhook"
  | "mock"
  | "none";

/** `GET /api/settings` (only the fields the UI reads). */
export interface ServerSettings {
  language: Language | null;
  brain: BrainKind | (string & {}) | null;
  assistant_name: string | null;
  mode: Mode | null;
}

// ---------------------------------------------------------------------------
// Field readers. Required readers throw ProtocolError; the top-level parser
// converts that into `{ ok: false }` so callers never see an exception.

class ProtocolError extends Error {}

type Obj = Record<string, unknown>;

function isObj(value: unknown): value is Obj {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function reqString(o: Obj, key: string): string {
  const value = o[key];
  if (typeof value !== "string") throw new ProtocolError(`${key}: expected string`);
  return value;
}

function optString(o: Obj, key: string): string | null {
  const value = o[key];
  return typeof value === "string" ? value : null;
}

function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function reqNumber(o: Obj, key: string): number {
  const value = o[key];
  if (!finite(value)) throw new ProtocolError(`${key}: expected number`);
  return value;
}

function optNumber(o: Obj, key: string): number | null {
  const value = o[key];
  return finite(value) ? value : null;
}

function reqUint32(o: Obj, key: string): number {
  const value = reqNumber(o, key);
  if (!Number.isInteger(value) || value < 0 || value > 0xffffffff) {
    throw new ProtocolError(`${key}: expected uint32`);
  }
  return value;
}

function optBool(o: Obj, key: string, fallback: boolean): boolean {
  const value = o[key];
  return typeof value === "boolean" ? value : fallback;
}

function clamp01(value: number): number {
  return value < 0 ? 0 : value > 1 ? 1 : value;
}

// ---------------------------------------------------------------------------

export function isMode(value: unknown): value is Mode {
  return value === "smart" || value === "always" || value === "wake";
}

/** Stand-in for a turn that arrived without a speaker; the view localizes its empty label. */
export const UNKNOWN_SPEAKER: Speaker = Object.freeze({
  id: UNKNOWN_SPEAKER_ID,
  label: "",
  name: null,
  name_state: "none",
  ordinal: null,
  persistent: false,
  present: true,
  turns: 0,
  color: -1,
}) as Speaker;

function normalizeColor(raw: unknown, ordinal: number | null): number {
  if (finite(raw)) {
    const index = Math.trunc(raw);
    if (index < 0) return -1;
    return index % SPEAKER_PALETTE_SIZE;
  }
  if (ordinal !== null && ordinal > 0) return (ordinal - 1) % SPEAKER_PALETTE_SIZE;
  return -1;
}

export function parseSpeaker(raw: unknown): Speaker {
  if (!isObj(raw)) throw new ProtocolError("speaker: expected object");
  const id = reqString(raw, "id");
  const name = optString(raw, "name");
  const nameStateRaw = raw.name_state;
  const name_state: NameState =
    nameStateRaw === "provisional" || nameStateRaw === "confirmed" ? nameStateRaw : "none";
  const ordinalRaw = optNumber(raw, "ordinal");
  const ordinal = ordinalRaw !== null && ordinalRaw > 0 ? Math.trunc(ordinalRaw) : null;
  // The server sends localized labels ("Person 3" / "3. kişi"). When one is missing,
  // derive only what needs no language; anything else stays empty for the view.
  let label = optString(raw, "label") ?? "";
  if (!label && id !== UNKNOWN_SPEAKER_ID && name) {
    label = name_state === "provisional" ? `${name}?` : name;
  }
  const turns = optNumber(raw, "turns");
  return {
    id,
    label,
    name,
    name_state,
    ordinal,
    persistent: optBool(raw, "persistent", false),
    present: optBool(raw, "present", true),
    turns: turns !== null && turns >= 0 ? Math.trunc(turns) : 0,
    color: id === UNKNOWN_SPEAKER_ID ? -1 : normalizeColor(raw.color, ordinal),
  };
}

function parseAttribution(raw: unknown): Attribution {
  if (!isObj(raw)) return { state: "known", score: null, margin: null };
  return {
    state: typeof raw.state === "string" ? raw.state : "known",
    score: optNumber(raw, "score"),
    margin: optNumber(raw, "margin"),
  };
}

export function parseTurn(raw: unknown): Turn {
  if (!isObj(raw)) throw new ProtocolError("turn: expected object");
  const id = reqString(raw, "id");
  const text = typeof raw.text === "string" ? raw.text : "";
  const speaker = raw.speaker == null ? UNKNOWN_SPEAKER : parseSpeaker(raw.speaker);
  const flags = Array.isArray(raw.flags) ? raw.flags.filter((f): f is string => typeof f === "string") : [];
  return {
    id,
    speaker,
    text,
    started_at: optNumber(raw, "started_at"),
    ended_at: optNumber(raw, "ended_at"),
    duration_s: optNumber(raw, "duration_s"),
    stt_ms: optNumber(raw, "stt_ms"),
    attribution: parseAttribution(raw.attribution),
    flags,
  };
}

function parseModels(raw: unknown): ModelMap {
  const models: ModelMap = {};
  if (!isObj(raw)) return models;
  for (const [key, value] of Object.entries(raw)) {
    if (!isObj(value)) continue;
    models[key] = {
      id: typeof value.id === "string" ? value.id : key,
      status: typeof value.status === "string" ? value.status : "loading",
      detail: optString(value, "detail"),
    };
  }
  return models;
}

function parseBudget(raw: unknown): Budget {
  if (!isObj(raw)) return { spent_usd: 0, cap_usd: 0, calls: 0 };
  return {
    spent_usd: optNumber(raw, "spent_usd") ?? 0,
    cap_usd: optNumber(raw, "cap_usd") ?? 0,
    calls: optNumber(raw, "calls") ?? 0,
  };
}

function parseUsage(raw: unknown): Usage | null {
  if (!isObj(raw)) return null;
  return {
    input_tokens: optNumber(raw, "input_tokens"),
    output_tokens: optNumber(raw, "output_tokens"),
    cost_usd: optNumber(raw, "cost_usd"),
  };
}

function parseList<T>(raw: unknown, parse: (item: unknown) => T): T[] {
  if (!Array.isArray(raw)) return [];
  const out: T[] = [];
  for (const item of raw) {
    try {
      out.push(parse(item));
    } catch {
      // Skip invalid entries; one bad row must not drop the whole list.
    }
  }
  return out;
}

function parseHistoryReply(raw: unknown): HistoryReply {
  if (!isObj(raw)) throw new ProtocolError("reply: expected object");
  return {
    reply_id: reqString(raw, "reply_id"),
    in_reply_to: optString(raw, "in_reply_to"),
    text: typeof raw.text === "string" ? raw.text : "",
    interrupted: optBool(raw, "interrupted", false),
  };
}

function parseObject(o: Obj, type: string): ServerMessage {
  switch (type) {
    case "ready":
      return {
        type,
        session_id: reqString(o, "session_id"),
        assistant_name: optString(o, "assistant_name") || "Lena",
        language: isLanguage(o.language) ? o.language : null,
        mode: isMode(o.mode) ? o.mode : "smart",
        listening: optBool(o, "listening", true),
        models: parseModels(o.models),
        budget: parseBudget(o.budget),
      };
    case "status":
      return { type, state: reqString(o, "state"), detail: optString(o, "detail") };
    case "level":
      return { type, rms: clamp01(optNumber(o, "rms") ?? 0), speech: optBool(o, "speech", false) };
    case "history":
      if (!Array.isArray(o.turns)) throw new ProtocolError("turns: expected array");
      return { type, turns: parseList(o.turns, parseTurn), replies: parseList(o.replies, parseHistoryReply) };
    case "turn":
      return { type, turn: parseTurn(o.turn) };
    case "turn_update":
      return { type, turn_id: reqString(o, "turn_id"), speaker: parseSpeaker(o.speaker) };
    case "speakers":
      if (!Array.isArray(o.speakers)) throw new ProtocolError("speakers: expected array");
      return { type, speakers: parseList(o.speakers, parseSpeaker) };
    case "identity_event":
      return {
        type,
        kind: reqString(o, "kind"),
        speaker_id: optString(o, "speaker_id"),
        name: optString(o, "name"),
        message: optString(o, "message") ?? "",
      };
    case "assistant_start":
      return {
        type,
        reply_id: reqString(o, "reply_id"),
        reply_seq: reqUint32(o, "reply_seq"),
        in_reply_to: optString(o, "in_reply_to"),
      };
    case "assistant_delta":
      return { type, reply_id: reqString(o, "reply_id"), text: reqString(o, "text") };
    case "audio_end": {
      const chunks = optNumber(o, "chunks");
      return {
        type,
        reply_id: reqString(o, "reply_id"),
        reply_seq: reqUint32(o, "reply_seq"),
        chunks: chunks !== null && chunks >= 0 ? Math.trunc(chunks) : 0,
      };
    }
    case "assistant_end":
      return {
        type,
        reply_id: reqString(o, "reply_id"),
        text: optString(o, "text") ?? "",
        interrupted: optBool(o, "interrupted", false),
        usage: parseUsage(o.usage),
      };
    case "assistant_silent":
      return { type, in_reply_to: optString(o, "in_reply_to"), reason: optString(o, "reason") ?? "not_addressed" };
    case "stop_playback":
      return { type, reply_id: optString(o, "reply_id"), reason: optString(o, "reason") ?? "barge_in" };
    case "usage":
      return {
        type,
        spent_usd: reqNumber(o, "spent_usd"),
        cap_usd: reqNumber(o, "cap_usd"),
        calls: optNumber(o, "calls") ?? 0,
      };
    case "notice": {
      const level = o.level === "warn" || o.level === "error" ? o.level : "info";
      return { type, level, code: optString(o, "code") ?? "", message: reqString(o, "message") };
    }
    case "pong":
      return { type, t: optNumber(o, "t") };
    default:
      return { type: "unknown", original_type: type };
  }
}

/** Parse one JSON text frame (string) or an already-decoded value. Never throws. */
export function parseServerMessage(input: unknown): ParseResult {
  let value = input;
  if (typeof input === "string") {
    try {
      value = JSON.parse(input);
    } catch {
      return { ok: false, error: "invalid_json" };
    }
  }
  if (!isObj(value)) return { ok: false, error: "not_an_object" };
  const type = value.type;
  if (typeof type !== "string" || type.length === 0) return { ok: false, error: "missing_type" };
  try {
    return { ok: true, message: parseObject(value, type) };
  } catch (error) {
    const detail = error instanceof ProtocolError ? error.message : "invalid_message";
    return { ok: false, error: `${type}: ${detail}` };
  }
}

export function encodeClientMessage(message: ClientMessage): string {
  return JSON.stringify(message);
}

export function helloMessage(): ClientMessage {
  return { type: "hello", client: CLIENT_NAME, version: PROTOCOL_VERSION, sample_rate: MIC_SAMPLE_RATE };
}

function parseTimestamp(value: unknown): number | null {
  if (finite(value)) {
    // Seconds (Python time.time()) vs milliseconds: anything below 1e11 is seconds.
    return value < 1e11 ? value * 1000 : value;
  }
  if (typeof value === "string" && value) {
    const parsed = Date.parse(value);
    return Number.isNaN(parsed) ? null : parsed;
  }
  return null;
}

export function parseProfiles(raw: unknown): Profile[] {
  const list = Array.isArray(raw) ? raw : isObj(raw) && Array.isArray(raw.profiles) ? raw.profiles : [];
  return parseList(list, (item) => {
    if (!isObj(item)) throw new ProtocolError("profile: expected object");
    const samples = optNumber(item, "samples");
    return {
      id: reqString(item, "id"),
      name: optString(item, "name") ?? "",
      created_at: parseTimestamp(item.created_at),
      updated_at: parseTimestamp(item.updated_at),
      samples: samples !== null && samples >= 0 ? Math.trunc(samples) : 0,
    };
  });
}

export function parseUsageResponse(raw: unknown): Budget | null {
  if (!isObj(raw)) return null;
  if (!finite(raw.spent_usd) || !finite(raw.cap_usd)) return null;
  return parseBudget(raw);
}

/** `GET /api/health`; null when the body is not an object. Missing fields become null. */
export function parseHealth(raw: unknown): Health | null {
  if (!isObj(raw)) return null;
  const gpu = isObj(raw.gpu)
    ? { name: optString(raw.gpu, "name"), available: optBool(raw.gpu, "available", false) }
    : null;
  return {
    status: optString(raw, "status") ?? "ok",
    version: optString(raw, "version"),
    language: isLanguage(raw.language) ? raw.language : null,
    models: parseModels(raw.models),
    hardware: optString(raw, "hardware"),
    gpu,
    profiles_key_storage: optString(raw, "profiles_key_storage"),
  };
}

/** `GET /api/settings`; null when the body is not an object. */
export function parseSettings(raw: unknown): ServerSettings | null {
  if (!isObj(raw)) return null;
  return {
    language: isLanguage(raw.language) ? raw.language : null,
    brain: optString(raw, "brain"),
    assistant_name: optString(raw, "assistant_name"),
    mode: isMode(raw.mode) ? raw.mode : null,
  };
}
