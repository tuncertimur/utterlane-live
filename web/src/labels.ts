// Protocol values → localized labels, plus small formatters. The copy itself lives in
// i18n.ts; these helpers add the fallbacks for values a newer backend may introduce.

import { isMessageKey, type Translator } from "./i18n.ts";
import { UNKNOWN_SPEAKER_ID, type Speaker } from "./protocol.ts";

/** Status pill text; "offline" is the UI's own state for a closed socket. */
export function statusLabel(i18n: Translator, state: string): string {
  const key = `status.${state}`;
  return isMessageKey(key) ? i18n.t(key) : i18n.t("status.ready");
}

export function silentLine(i18n: Translator, assistant: string, reason: string): string {
  const key = `silentReason.${reason}`;
  const text = isMessageKey(key) ? i18n.t(key, { assistant }) : i18n.t("silentReason.other");
  return i18n.t("silent.line", { assistant, reason: text });
}

export type BadgeTone = "warn" | "muted" | "info" | "accent" | "ok";
export interface Badge {
  label: string;
  tone: BadgeTone;
  hint?: string;
}

const ATTRIBUTION_TONES: Record<string, BadgeTone> = { short: "muted", ambiguous: "warn", overlap: "warn" };
const FLAG_TONES: Record<string, BadgeTone> = {
  self_intro: "accent",
  nomination: "accent",
  correction: "warn",
  consent_yes: "ok",
  consent_no: "muted",
  barge_in: "muted",
};

/** Badge for a doubtful attribution (`short`, `ambiguous`, `overlap`), else null. */
export function attributionBadge(i18n: Translator, state: string): Badge | null {
  const tone = ATTRIBUTION_TONES[state];
  const key = `badge.${state}`;
  const hint = `badge.${state}.hint`;
  if (!tone || !isMessageKey(key)) return null;
  return { label: i18n.t(key), tone, hint: isMessageKey(hint) ? i18n.t(hint) : undefined };
}

/** Badge for a turn flag; `addressed_assistant` has its own icon and gets none. */
export function flagBadge(i18n: Translator, flag: string): Badge | null {
  const tone = FLAG_TONES[flag];
  const key = `badge.${flag}`;
  if (!tone || !isMessageKey(key)) return null;
  return { label: i18n.t(key), tone };
}

export function identityKindLabel(i18n: Translator, kind: string): string {
  const key = `identity.${kind}`;
  return isMessageKey(key) ? i18n.t(key) : kind;
}

export function nameStateLabel(i18n: Translator, state: string): string {
  const key = `nameState.${state}`;
  return isMessageKey(key) ? i18n.t(key) : i18n.t("nameState.none");
}

export function modelLabel(i18n: Translator, model: string): string {
  const key = `model.${model}`;
  return isMessageKey(key) ? i18n.t(key) : model;
}

export function modelStatusLabel(i18n: Translator, status: string): string {
  const key = `modelStatus.${status}`;
  return isMessageKey(key) ? i18n.t(key) : status;
}

export function noticeTitle(i18n: Translator, level: string): string {
  const key = `notice.${level}`;
  return isMessageKey(key) ? i18n.t(key) : i18n.t("notice.info");
}

/** Brain kind from `/api/settings` (`openai`, `ollama`, `none` …); unknown kinds as-is. */
export function brainLabel(i18n: Translator, kind: string): string {
  const key = `brainKind.${kind}`;
  return isMessageKey(key) ? i18n.t(key) : kind;
}

export function keyStorageLabel(i18n: Translator, storage: string): string {
  const key = `keyStorage.${storage}`;
  return isMessageKey(key) ? i18n.t(key) : storage;
}

export function captureError(i18n: Translator, kind: string, url: string): { title: string; body: string } {
  const known = isMessageKey(`capture.${kind}.title`) ? kind : "unknown";
  const title = `capture.${known}.title`;
  const body = `capture.${known}.body`;
  return {
    title: isMessageKey(title) ? i18n.t(title) : kind,
    body: isMessageKey(body) ? i18n.t(body, { url }) : "",
  };
}

/**
 * What to call a speaker. The server sends a localized label; the fallbacks cover a
 * speaker that arrived without one (a turn with no speaker, an older backend).
 */
export function speakerLabel(i18n: Translator, speaker: Speaker): string {
  if (speaker.label) return speaker.label;
  if (speaker.id === UNKNOWN_SPEAKER_ID) return i18n.t("speaker.unknown");
  if (speaker.ordinal !== null) return i18n.t("speaker.person", { n: speaker.ordinal });
  return i18n.t("speaker.generic");
}

export type ChipVariant = "named" | "provisional" | "ordinal" | "unknown";

export function chipVariant(speaker: Speaker): ChipVariant {
  if (speaker.id === UNKNOWN_SPEAKER_ID) return "unknown";
  if (speaker.name_state === "provisional") return "provisional";
  if (speaker.name_state === "confirmed" && speaker.name) return "named";
  return "ordinal";
}

const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 4 });

export function formatUsd(value: number): string {
  if (!Number.isFinite(value)) return "$0.00";
  return usd.format(value);
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

/** Local wall clock "HH:MM" for an epoch in milliseconds. */
export function formatClock(epochMs: number | null, withSeconds = false): string {
  if (epochMs === null || !Number.isFinite(epochMs)) return "";
  const date = new Date(epochMs);
  const base = `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  return withSeconds ? `${base}:${pad(date.getSeconds())}` : base;
}

export function formatDate(epochMs: number | null, locale: string): string {
  if (epochMs === null || !Number.isFinite(epochMs)) return "—";
  try {
    return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(new Date(epochMs));
  } catch {
    return new Date(epochMs).toISOString();
  }
}
