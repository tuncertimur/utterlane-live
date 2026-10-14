// Pure reducer: server events + local actions → UI state. No timers, no Date.now():
// every action carries its own timestamp so scenarios are reproducible in tests.

import type { MessageKey } from "./i18n.ts";
import {
  UNKNOWN_SPEAKER_ID,
  type Budget,
  type HistoryReply,
  type Language,
  type Mode,
  type ModelMap,
  type NoticeLevel,
  type ServerMessage,
  type Speaker,
  type Turn,
  type Usage,
} from "./protocol.ts";

/** `replaced`: another tab took over the single server session; we stay idle until asked. */
export type ConnectionState = "idle" | "connecting" | "open" | "reconnecting" | "closed" | "replaced";

export interface Relabel {
  /** Speaker shown before the change. */
  from: Speaker;
  /** Monotonic counter; changes on every relabel so views can replay animations. */
  seq: number;
  at: number;
}

export interface TurnItem {
  kind: "turn";
  id: string;
  turn: Turn;
  relabel: Relabel | null;
  at: number;
}

export type ReplyAudio = "none" | "playing" | "done" | "stopped";

export interface ReplyItem {
  kind: "reply";
  id: string;
  replySeq: number | null;
  inReplyTo: string | null;
  text: string;
  streaming: boolean;
  interrupted: boolean;
  audio: ReplyAudio;
  usage: Usage | null;
  at: number;
}

/** `reason: "no_brain"` is the listen-only system line, added at most once per session. */
export interface SilentItem {
  kind: "silent";
  id: string;
  inReplyTo: string | null;
  reason: string;
  at: number;
}

export type TimelineItem = TurnItem | ReplyItem | SilentItem;

export interface IdentityLogEntry {
  id: number;
  kind: string;
  speakerId: string | null;
  name: string | null;
  message: string;
  at: number;
}

export interface Toast {
  id: number;
  level: NoticeLevel;
  code: string;
  /** Server notices arrive localized; shown as-is. */
  message: string;
  /** Local toasts carry a dictionary key instead, translated when rendered. */
  key: MessageKey | null;
  at: number;
}

export interface LiveState {
  connection: ConnectionState;
  retryInMs: number | null;
  connectedOnce: boolean;
  sessionId: string | null;
  assistantName: string;
  /** `ready.language`: the server's conversation language, null until a ready carries it. */
  language: Language | null;
  mode: Mode;
  listening: boolean;
  status: { state: string; detail: string | null };
  models: ModelMap;
  budget: Budget;
  speech: boolean;
  items: TimelineItem[];
  speakers: Speaker[];
  identityLog: IdentityLogEntry[];
  toasts: Toast[];
  rttMs: number | null;
  /** The listen-only line (`assistant_silent` reason `no_brain`) was shown this session. */
  listenOnlyNoted: boolean;
  seq: number;
}

export type LiveAction =
  | { type: "server"; message: ServerMessage; at: number }
  | { type: "connection"; state: ConnectionState; retryInMs?: number | null; at: number }
  | { type: "local_playback"; replyId: string | null; state: "started" | "ended" | "stopped"; at: number }
  | { type: "set_mode"; mode: Mode }
  | { type: "set_listening"; enabled: boolean }
  | { type: "toast"; level: NoticeLevel; message?: string; key?: MessageKey; code?: string; at: number }
  | { type: "dismiss_toast"; id: number }
  | { type: "reset_local" };

export const MAX_IDENTITY_LOG = 60;
export const MAX_TOASTS = 4;
export const MAX_ITEMS = 500;

export function createInitialState(): LiveState {
  return {
    connection: "idle",
    retryInMs: null,
    connectedOnce: false,
    sessionId: null,
    assistantName: "Lena",
    language: null,
    mode: "smart",
    listening: true,
    status: { state: "loading", detail: null },
    models: {},
    budget: { spent_usd: 0, cap_usd: 0, calls: 0 },
    speech: false,
    items: [],
    speakers: [],
    identityLog: [],
    toasts: [],
    rttMs: null,
    listenOnlyNoted: false,
    seq: 0,
  };
}

// ---------------------------------------------------------------------------
// Helpers

function sameLabel(a: Speaker, b: Speaker): boolean {
  return a.id === b.id && a.label === b.label && a.name_state === b.name_state && a.color === b.color;
}

function indexOfItem(items: TimelineItem[], kind: TimelineItem["kind"], id: string): number {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (item.kind === kind && item.id === id) return index;
  }
  return -1;
}

function capItems(items: TimelineItem[]): TimelineItem[] {
  return items.length > MAX_ITEMS ? items.slice(items.length - MAX_ITEMS) : items;
}

function replaceAt<T>(list: T[], index: number, value: T): T[] {
  const next = list.slice();
  next[index] = value;
  return next;
}

function updateReply(state: LiveState, replyId: string, update: (reply: ReplyItem) => ReplyItem): LiveState {
  const index = indexOfItem(state.items, "reply", replyId);
  if (index < 0) return state;
  const reply = state.items[index] as ReplyItem;
  const next = update(reply);
  return next === reply ? state : { ...state, items: replaceAt(state.items, index, next) };
}

function newReply(id: string, at: number, partial: Partial<ReplyItem> = {}): ReplyItem {
  return {
    kind: "reply",
    id,
    replySeq: null,
    inReplyTo: null,
    text: "",
    streaming: true,
    interrupted: false,
    audio: "none",
    usage: null,
    at,
    ...partial,
  };
}

function pushToast(
  state: LiveState,
  level: NoticeLevel,
  message: string,
  code: string,
  at: number,
  key: MessageKey | null = null,
): LiveState {
  const seq = state.seq + 1;
  // Collapse an identical toast that is still visible instead of stacking duplicates.
  const toasts = state.toasts.filter((toast) => !(toast.code === code && toast.message === message && toast.key === key));
  toasts.push({ id: seq, level, code, message, key, at });
  return { ...state, seq, toasts: toasts.slice(-MAX_TOASTS) };
}

function withRelabel(state: LiveState, item: TurnItem, speaker: Speaker, at: number): { state: LiveState; item: TurnItem } {
  if (sameLabel(item.turn.speaker, speaker)) {
    return { state, item: { ...item, turn: { ...item.turn, speaker } } };
  }
  const seq = state.seq + 1;
  return {
    state: { ...state, seq },
    item: { ...item, turn: { ...item.turn, speaker }, relabel: { from: item.turn.speaker, seq, at } },
  };
}

/**
 * `history` is the server's authoritative window of recent turns (sent on connect and,
 * with `turns: []`, after `reset_session`). Reconcile the local timeline with it:
 *  - an empty history clears the timeline;
 *  - local turns older than the window are kept;
 *  - inside the window the server's order wins; local turns it no longer has are dropped;
 *  - replies and silent markers stay attached to the turn they followed.
 */
function reconcileHistory(
  items: TimelineItem[],
  turns: Turn[],
  replies: HistoryReply[],
  at: number,
  seq: number,
): { items: TimelineItem[]; seq: number } {
  if (turns.length === 0 && replies.length === 0) return { items: [], seq };

  // Split the local timeline into segments: each turn owns the non-turn items after it.
  const prefix: TimelineItem[] = [];
  const segments: { turn: TurnItem; tail: TimelineItem[] }[] = [];
  for (const item of items) {
    if (item.kind === "turn") segments.push({ turn: item, tail: [] });
    else (segments.length ? segments[segments.length - 1].tail : prefix).push(item);
  }
  const historyIds = new Set(turns.map((turn) => turn.id));
  const firstKnown = segments.findIndex((segment) => historyIds.has(segment.turn.id));
  const bySegmentId = new Map(segments.map((segment) => [segment.turn.id, segment]));

  const next: TimelineItem[] = [...prefix];
  if (firstKnown > 0) {
    // Older than the server's window: keep as-is.
    for (const segment of segments.slice(0, firstKnown)) next.push(segment.turn, ...segment.tail);
  }
  for (const turn of turns) {
    const segment = bySegmentId.get(turn.id);
    if (!segment) {
      next.push({ kind: "turn", id: turn.id, turn, relabel: null, at });
      continue;
    }
    const local = segment.turn;
    if (sameLabel(local.turn.speaker, turn.speaker)) {
      next.push({ ...local, turn });
    } else {
      seq += 1;
      next.push({ ...local, turn, relabel: { from: local.turn.speaker, seq, at } });
    }
    next.push(...segment.tail);
  }

  for (const reply of replies) {
    if (indexOfItem(next, "reply", reply.reply_id) >= 0) continue;
    const item = newReply(reply.reply_id, at, {
      inReplyTo: reply.in_reply_to,
      text: reply.text,
      streaming: false,
      interrupted: reply.interrupted,
      audio: "done",
    });
    let position = next.length;
    if (reply.in_reply_to) {
      const turnIndex = indexOfItem(next, "turn", reply.in_reply_to);
      if (turnIndex >= 0) {
        position = turnIndex + 1;
        while (position < next.length && next[position].kind !== "turn") position += 1;
      }
    }
    next.splice(position, 0, item);
  }
  return { items: capItems(next), seq };
}

function deriveStatusFromReady(models: ModelMap, listening: boolean): string {
  const values = Object.values(models);
  if (values.some((model) => model.status === "loading")) return "loading";
  return listening ? "listening" : "paused";
}

// ---------------------------------------------------------------------------

function reduceServer(state: LiveState, message: ServerMessage, at: number): LiveState {
  switch (message.type) {
    case "ready": {
      const sessionChanged = state.sessionId !== null && state.sessionId !== message.session_id;
      const base: LiveState = sessionChanged
        ? { ...state, items: [], speakers: [], identityLog: [], listenOnlyNoted: false }
        : state;
      return {
        ...base,
        sessionId: message.session_id,
        assistantName: message.assistant_name,
        language: message.language ?? base.language,
        mode: message.mode,
        listening: message.listening,
        models: message.models,
        budget: message.budget,
        status: { state: deriveStatusFromReady(message.models, message.listening), detail: null },
      };
    }
    case "status":
      if (state.status.state === message.state && state.status.detail === message.detail) return state;
      return {
        ...state,
        status: { state: message.state, detail: message.detail },
        listening: message.state === "paused" ? false : message.state === "listening" ? true : state.listening,
      };
    case "level":
      return state.speech === message.speech ? state : { ...state, speech: message.speech };
    case "history": {
      const result = reconcileHistory(state.items, message.turns, message.replies, at, state.seq);
      // An empty history is the reset signal: a fresh conversation may note listen-only again.
      const cleared = result.items.length === 0;
      return {
        ...state,
        items: result.items,
        seq: result.seq,
        listenOnlyNoted: cleared ? false : state.listenOnlyNoted,
      };
    }
    case "turn": {
      const index = indexOfItem(state.items, "turn", message.turn.id);
      if (index >= 0) {
        const item = state.items[index] as TurnItem;
        const result = withRelabel(state, { ...item, turn: { ...message.turn, speaker: item.turn.speaker } }, message.turn.speaker, at);
        return { ...result.state, items: replaceAt(state.items, index, result.item) };
      }
      const item: TurnItem = { kind: "turn", id: message.turn.id, turn: message.turn, relabel: null, at };
      return { ...state, items: capItems([...state.items, item]) };
    }
    case "turn_update": {
      const index = indexOfItem(state.items, "turn", message.turn_id);
      if (index < 0) return state;
      const result = withRelabel(state, state.items[index] as TurnItem, message.speaker, at);
      return { ...result.state, items: replaceAt(result.state.items, index, result.item) };
    }
    case "speakers": {
      // Propagate label changes (rename, provisional → confirmed) to existing turns
      // so the timeline always shows current knowledge, with a visible relabel.
      const byId = new Map(message.speakers.map((speaker) => [speaker.id, speaker]));
      let next: LiveState = { ...state, speakers: message.speakers };
      let items: TimelineItem[] = state.items;
      for (let index = 0; index < state.items.length; index += 1) {
        const item = state.items[index];
        if (item.kind !== "turn" || item.turn.speaker.id === UNKNOWN_SPEAKER_ID) continue;
        const current = byId.get(item.turn.speaker.id);
        if (!current || sameLabel(item.turn.speaker, current)) continue;
        const result = withRelabel(next, item, current, at);
        next = result.state;
        if (items === state.items) items = state.items.slice();
        items[index] = result.item;
      }
      return items === state.items ? next : { ...next, items };
    }
    case "identity_event": {
      const seq = state.seq + 1;
      const entry: IdentityLogEntry = {
        id: seq,
        kind: message.kind,
        speakerId: message.speaker_id,
        name: message.name,
        message: message.message,
        at,
      };
      return { ...state, seq, identityLog: [entry, ...state.identityLog].slice(0, MAX_IDENTITY_LOG) };
    }
    case "assistant_start": {
      const index = indexOfItem(state.items, "reply", message.reply_id);
      if (index >= 0) {
        const reply = state.items[index] as ReplyItem;
        return {
          ...state,
          items: replaceAt(state.items, index, { ...reply, replySeq: message.reply_seq, inReplyTo: message.in_reply_to }),
        };
      }
      const reply = newReply(message.reply_id, at, { replySeq: message.reply_seq, inReplyTo: message.in_reply_to });
      return { ...state, items: capItems([...state.items, reply]) };
    }
    case "assistant_delta": {
      const index = indexOfItem(state.items, "reply", message.reply_id);
      if (index < 0) {
        const reply = newReply(message.reply_id, at, { text: message.text });
        return { ...state, items: capItems([...state.items, reply]) };
      }
      return updateReply(state, message.reply_id, (reply) =>
        reply.streaming ? { ...reply, text: reply.text + message.text } : reply,
      );
    }
    case "assistant_end": {
      const index = indexOfItem(state.items, "reply", message.reply_id);
      if (index < 0) {
        if (!message.text) return state;
        const reply = newReply(message.reply_id, at, {
          text: message.text,
          streaming: false,
          interrupted: message.interrupted,
          usage: message.usage,
        });
        return { ...state, items: capItems([...state.items, reply]) };
      }
      return updateReply(state, message.reply_id, (reply) => ({
        ...reply,
        text: message.text || reply.text,
        streaming: false,
        interrupted: reply.interrupted || message.interrupted,
        usage: message.usage ?? reply.usage,
      }));
    }
    case "audio_end":
      return state;
    case "stop_playback": {
      if (!message.reply_id) return state;
      return updateReply(state, message.reply_id, (reply) => ({
        ...reply,
        audio: reply.audio === "done" ? reply.audio : "stopped",
        interrupted: reply.interrupted || message.reason === "barge_in",
      }));
    }
    case "assistant_silent": {
      if (message.reason === "no_brain") {
        // Listen-only mode: every turn is silent. Say so once per session, not per turn.
        if (state.listenOnlyNoted) return state;
        const item: SilentItem = {
          kind: "silent",
          id: `listen-only:${state.seq + 1}`,
          inReplyTo: message.in_reply_to,
          reason: message.reason,
          at,
        };
        return { ...state, seq: state.seq + 1, listenOnlyNoted: true, items: capItems([...state.items, item]) };
      }
      const id = `silent:${message.in_reply_to ?? `seq-${state.seq + 1}`}`;
      if (indexOfItem(state.items, "silent", id) >= 0) return state;
      const item: SilentItem = { kind: "silent", id, inReplyTo: message.in_reply_to, reason: message.reason, at };
      return { ...state, seq: state.seq + 1, items: capItems([...state.items, item]) };
    }
    case "usage":
      return { ...state, budget: { spent_usd: message.spent_usd, cap_usd: message.cap_usd, calls: message.calls } };
    case "notice":
      return pushToast(state, message.level, message.message, message.code, at);
    case "pong":
      return message.t !== null ? { ...state, rttMs: Math.max(0, at - message.t) } : state;
    case "unknown":
      return state;
  }
}

export function liveReducer(state: LiveState, action: LiveAction): LiveState {
  switch (action.type) {
    case "server":
      return reduceServer(state, action.message, action.at);
    case "connection": {
      const next: LiveState = {
        ...state,
        connection: action.state,
        retryInMs: action.retryInMs ?? null,
        connectedOnce: state.connectedOnce || action.state === "open",
      };
      if (action.state === "open" || action.state === "connecting") return next;
      // Socket lost: anything still streaming will never get its end message.
      let changed = false;
      const items = next.items.map((item) => {
        if (item.kind !== "reply" || (!item.streaming && item.audio !== "playing")) return item;
        changed = true;
        return {
          ...item,
          streaming: false,
          interrupted: item.streaming || item.interrupted,
          audio: item.audio === "playing" ? ("stopped" as const) : item.audio,
        };
      });
      return changed ? { ...next, items, speech: false } : { ...next, speech: false };
    }
    case "local_playback": {
      if (!action.replyId) return state;
      const audio: ReplyAudio = action.state === "started" ? "playing" : action.state === "ended" ? "done" : "stopped";
      return updateReply(state, action.replyId, (reply) =>
        reply.audio === audio
          ? reply
          : { ...reply, audio, interrupted: reply.interrupted || (action.state === "stopped" && reply.audio === "playing") },
      );
    }
    case "set_mode":
      return state.mode === action.mode ? state : { ...state, mode: action.mode };
    case "set_listening":
      return state.listening === action.enabled ? state : { ...state, listening: action.enabled };
    case "toast":
      return pushToast(state, action.level, action.message ?? "", action.code ?? "local", action.at, action.key ?? null);
    case "dismiss_toast":
      return { ...state, toasts: state.toasts.filter((toast) => toast.id !== action.id) };
    case "reset_local":
      return { ...state, items: [], identityLog: [], listenOnlyNoted: false };
  }
}

// ---------------------------------------------------------------------------
// Selectors

export function lastReplyPlaying(state: LiveState): ReplyItem | null {
  for (let index = state.items.length - 1; index >= 0; index -= 1) {
    const item = state.items[index];
    if (item.kind === "reply" && item.audio === "playing") return item;
  }
  return null;
}

/**
 * Status to display. The server keeps `speaking` until its reply task finishes, even
 * after this client stopped or finished playback; nothing is audible then, so show
 * the listening state instead of a stale "Speaking".
 */
export function effectiveStatus(state: LiveState): string {
  const status = state.status.state;
  if (status === "speaking" && lastReplyPlaying(state) === null) return state.listening ? "listening" : "paused";
  return status;
}

/**
 * Listen-only mode: no AI brain, so the server transcribes and labels speakers but never
 * replies. Known from `models.llm.status === "off"` (also when OPENAI_API_KEY is missing),
 * from `/api/settings` `brain: "none"`, or from a `no_brain` silence.
 */
export function isListenOnly(state: LiveState, brain: string | null = null): boolean {
  return state.models.llm?.status === "off" || brain === "none" || state.listenOnlyNoted;
}

export function modelProblems(models: ModelMap): { key: string; detail: string | null }[] {
  return Object.entries(models)
    .filter(([, model]) => model.status === "error")
    .map(([key, model]) => ({ key, detail: model.detail }));
}

export function sortRoster(speakers: Speaker[]): Speaker[] {
  return speakers
    .filter((speaker) => speaker.id !== UNKNOWN_SPEAKER_ID)
    .slice()
    .sort((a, b) => {
      if (a.present !== b.present) return a.present ? -1 : 1;
      return (a.ordinal ?? 999) - (b.ordinal ?? 999);
    });
}
