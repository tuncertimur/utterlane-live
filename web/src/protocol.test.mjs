import assert from "node:assert/strict";
import test from "node:test";
import {
  UNKNOWN_SPEAKER,
  encodeClientMessage,
  helloMessage,
  parseHealth,
  parseProfiles,
  parseServerMessage,
  parseSettings,
  parseUsageResponse,
} from "./protocol.ts";

const speaker = (overrides = {}) => ({
  id: "spk-3",
  label: "3. kişi",
  name: null,
  name_state: "none",
  ordinal: 3,
  persistent: false,
  present: true,
  turns: 4,
  color: 2,
  ...overrides,
});

const turn = (overrides = {}) => ({
  id: "t-000012",
  speaker: speaker(),
  text: "Ben Tuncer, bugün hava nasıl olacak?",
  started_at: 1739999999.12,
  ended_at: 1740000001.8,
  duration_s: 2.68,
  stt_ms: 310,
  attribution: { state: "known", score: 0.71, margin: 0.33 },
  flags: ["self_intro"],
  ...overrides,
});

function ok(input) {
  const result = parseServerMessage(input);
  assert.equal(result.ok, true, result.ok ? "" : result.error);
  return result.message;
}

function fails(input) {
  const result = parseServerMessage(input);
  assert.equal(result.ok, false);
  return result.error;
}

test("ready parses with defaults and ignores unknown fields", () => {
  const message = ok(
    JSON.stringify({
      type: "ready",
      session_id: "s-1",
      assistant_name: "Lena",
      mode: "wake",
      listening: false,
      models: { stt: { id: "whisper-tr", status: "ready" }, tts: { id: "qwen", status: "error", detail: "CUDA yok" }, bogus: 5 },
      budget: { spent_usd: 0.25, cap_usd: 5 },
      future_field: { anything: true },
    }),
  );
  assert.equal(message.type, "ready");
  assert.equal(message.mode, "wake");
  assert.equal(message.listening, false);
  assert.deepEqual(Object.keys(message.models), ["stt", "tts"]);
  assert.equal(message.models.tts.detail, "CUDA yok");
  assert.deepEqual(message.budget, { spent_usd: 0.25, cap_usd: 5, calls: 0 });
  assert.equal("future_field" in message, false);
});

test("ready falls back to safe defaults for odd values", () => {
  const message = ok({ type: "ready", session_id: "s-2", mode: "chaos" });
  assert.equal(message.mode, "smart");
  assert.equal(message.assistant_name, "Lena");
  assert.equal(message.listening, true);
  assert.deepEqual(message.models, {});
  assert.equal(message.language, null, "an older backend without `language` leaves it unknown");
});

test("ready carries the server's conversation language; unknown values are ignored", () => {
  assert.equal(ok({ type: "ready", session_id: "s", language: "en" }).language, "en");
  assert.equal(ok({ type: "ready", session_id: "s", language: "tr" }).language, "tr");
  assert.equal(ok({ type: "ready", session_id: "s", language: "de" }).language, null);
  assert.equal(ok({ type: "ready", session_id: "s", language: "EN" }).language, null);
});

test("listen-only mode: llm status `off` and the `no_brain` silence reason pass through", () => {
  const ready = ok({ type: "ready", session_id: "s", models: { llm: { id: "none (listen only)", status: "off" } } });
  assert.equal(ready.models.llm.status, "off");
  assert.equal(ok({ type: "assistant_silent", in_reply_to: "t-1", reason: "no_brain" }).reason, "no_brain");
  assert.equal(ok({ type: "assistant_silent", in_reply_to: "t-1", reason: "speaker_not_allowed" }).reason, "speaker_not_allowed");
});

test("turn parses fully, including Turkish text and flags", () => {
  const message = ok({ type: "turn", turn: turn({ flags: ["self_intro", 7, "consent_yes"] }) });
  assert.equal(message.type, "turn");
  assert.equal(message.turn.text, "Ben Tuncer, bugün hava nasıl olacak?");
  assert.equal(message.turn.speaker.label, "3. kişi");
  assert.deepEqual(message.turn.flags, ["self_intro", "consent_yes"]);
  assert.equal(message.turn.attribution.score, 0.71);
});

test("turn without speaker is attributed to the ambiguous pseudo-speaker", () => {
  const message = ok({ type: "turn", turn: turn({ speaker: null, attribution: { state: "ambiguous" } }) });
  assert.deepEqual(message.turn.speaker, UNKNOWN_SPEAKER);
  assert.equal(message.turn.attribution.state, "ambiguous");
  assert.equal(message.turn.attribution.score, null);
});

test("speaker colours are normalised; missing labels are derived only when language-neutral", () => {
  const unknown = ok({ type: "turn_update", turn_id: "t-1", speaker: { id: "unknown", label: "Belirsiz", color: -1, ordinal: 0 } });
  assert.equal(unknown.speaker.color, -1);
  assert.equal(unknown.speaker.ordinal, null);
  assert.equal(unknown.speaker.label, "Belirsiz", "server labels are kept verbatim");
  const english = ok({ type: "turn_update", turn_id: "t-1", speaker: speaker({ label: "Person 3" }) });
  assert.equal(english.speaker.label, "Person 3");
  const wrapped = ok({ type: "turn_update", turn_id: "t-1", speaker: speaker({ color: 9 }) });
  assert.equal(wrapped.speaker.color, 1);
  const derived = ok({ type: "turn_update", turn_id: "t-1", speaker: { id: "spk-5", ordinal: 5, name: "Ahmet", name_state: "provisional" } });
  assert.equal(derived.speaker.label, "Ahmet?");
  assert.equal(derived.speaker.color, 4);
  const named = ok({ type: "turn_update", turn_id: "t-1", speaker: { id: "spk-7", ordinal: 7, name: "Zümra", name_state: "confirmed" } });
  assert.equal(named.speaker.label, "Zümra");
  // "Person 6" / "6. kişi" depends on the language: the parser leaves it to the view.
  const ordinal = ok({ type: "turn_update", turn_id: "t-1", speaker: { id: "spk-6", ordinal: 6, name_state: "weird" } });
  assert.equal(ordinal.speaker.label, "");
  assert.equal(ordinal.speaker.ordinal, 6);
  assert.equal(ordinal.speaker.name_state, "none");
  const bareUnknown = ok({ type: "turn_update", turn_id: "t-1", speaker: { id: "unknown" } });
  assert.equal(bareUnknown.speaker.label, "");
});

test("speakers list keeps valid rows and skips invalid ones", () => {
  const message = ok({ type: "speakers", speakers: [speaker(), { label: "no id" }, null, speaker({ id: "spk-1", label: "Tuncer", name: "Tuncer", name_state: "confirmed", persistent: true })] });
  assert.equal(message.speakers.length, 2);
  assert.equal(message.speakers[1].persistent, true);
});

test("history tolerates bad turns and accepts optional replies", () => {
  const message = ok({
    type: "history",
    turns: [turn({ id: "t-1" }), { text: "no id" }, turn({ id: "t-2" })],
    replies: [{ reply_id: "r-1", in_reply_to: "t-1", text: "Merhaba!", interrupted: false }, { nope: true }],
  });
  assert.deepEqual(message.turns.map((t) => t.id), ["t-1", "t-2"]);
  assert.equal(message.replies.length, 1);
  const bare = ok({ type: "history", turns: [] });
  assert.deepEqual(bare.replies, []);
});

test("assistant stream messages parse", () => {
  assert.deepEqual(ok({ type: "assistant_start", reply_id: "r-1", reply_seq: 4, in_reply_to: null }), {
    type: "assistant_start",
    reply_id: "r-1",
    reply_seq: 4,
    in_reply_to: null,
  });
  assert.equal(ok({ type: "assistant_delta", reply_id: "r-1", text: "Merhaba " }).text, "Merhaba ");
  const end = ok({ type: "assistant_end", reply_id: "r-1", text: "Merhaba Tuncer.", interrupted: true, usage: { input_tokens: 120, output_tokens: 18, cost_usd: 0.0004 } });
  assert.equal(end.interrupted, true);
  assert.equal(end.usage.output_tokens, 18);
  assert.equal(ok({ type: "audio_end", reply_id: "r-1", reply_seq: 4, chunks: 3 }).chunks, 3);
  assert.equal(ok({ type: "assistant_silent", in_reply_to: "t-9", reason: "not_addressed" }).reason, "not_addressed");
  assert.equal(ok({ type: "stop_playback", reply_id: "r-1", reason: "barge_in" }).reason, "barge_in");
});

test("invalid messages are rejected without throwing", () => {
  assert.equal(fails("{not json"), "invalid_json");
  assert.equal(fails("[1,2]"), "not_an_object");
  assert.equal(fails(42), "not_an_object");
  assert.equal(fails({ text: "no type" }), "missing_type");
  assert.match(fails({ type: "assistant_start", reply_id: "r", reply_seq: -1 }), /reply_seq/);
  assert.match(fails({ type: "assistant_start", reply_id: "r", reply_seq: 1.5 }), /reply_seq/);
  assert.match(fails({ type: "turn", turn: { text: "id yok" } }), /id/);
  assert.match(fails({ type: "usage", spent_usd: 1 }), /cap_usd/);
  assert.match(fails({ type: "notice", level: "info" }), /message/);
  assert.match(fails({ type: "speakers", speakers: "all" }), /speakers/);
  assert.match(fails({ type: "status" }), /state/);
});

test("unknown types and enum values are preserved, not fatal", () => {
  assert.deepEqual(ok({ type: "hologram", payload: 1 }), { type: "unknown", original_type: "hologram" });
  assert.equal(ok({ type: "status", state: "warming_up" }).state, "warming_up");
  assert.equal(ok({ type: "identity_event", kind: "teleported", speaker_id: "spk-1", message: "?" }).kind, "teleported");
  assert.equal(ok({ type: "notice", level: "shout", message: "Merhaba" }).level, "info");
  assert.equal(ok({ type: "level", rms: 3, speech: true }).rms, 1);
});

test("client messages encode as JSON", () => {
  assert.deepEqual(JSON.parse(encodeClientMessage(helloMessage())), {
    type: "hello",
    client: "utterlane-live-web",
    version: 1,
    sample_rate: 16000,
  });
  assert.equal(encodeClientMessage({ type: "rename_speaker", speaker_id: "spk-2", name: "Zümra" }), '{"type":"rename_speaker","speaker_id":"spk-2","name":"Zümra"}');
});

test("profiles accept epoch seconds and ISO strings", () => {
  const profiles = parseProfiles([
    { id: "p-1", name: "Tuncer", created_at: 1760000000.5, updated_at: 1760000100, samples: 3 },
    { id: "p-2", name: "Zümra", created_at: "2026-10-14T09:00:00Z", updated_at: null, samples: "x" },
    { name: "id yok" },
  ]);
  assert.equal(profiles.length, 2);
  assert.equal(profiles[0].created_at, 1760000000500);
  assert.equal(profiles[1].created_at, Date.parse("2026-10-14T09:00:00Z"));
  assert.equal(profiles[1].updated_at, null);
  assert.equal(profiles[1].samples, 0);
  assert.deepEqual(parseProfiles({ profiles: [{ id: "p-3" }] }).map((p) => p.name), [""], "the view shows a localized placeholder");
  assert.deepEqual(parseUsageResponse({ spent_usd: 1, cap_usd: 5, calls: 3 }), { spent_usd: 1, cap_usd: 5, calls: 3 });
  assert.equal(parseUsageResponse({ spent_usd: "1" }), null);
});

test("health parses the full answer and tolerates the loading one", () => {
  const health = parseHealth({
    status: "ok",
    version: "0.1.0",
    language: "en",
    models: {
      stt: { id: "whisper-large-v3-turbo (cuda)", status: "ready", detail: "CUDA GPU detected" },
      tts: { id: "pocket:alba", status: "ready", detail: "Voice: Kyutai Pocket TTS (CC-BY-4.0)." },
      llm: { id: "none (OPENAI_API_KEY not set)", status: "off" },
    },
    hardware: "Windows amd64, 16 CPU threads, CUDA GPU (RTX 4090)",
    gpu: { name: "RTX 4090", available: true },
    profiles_key_storage: "os_credential_store",
  });
  assert.equal(health.language, "en");
  assert.equal(health.models.tts.detail, "Voice: Kyutai Pocket TTS (CC-BY-4.0).");
  assert.equal(health.models.llm.status, "off");
  assert.deepEqual(health.gpu, { name: "RTX 4090", available: true });
  assert.equal(health.profiles_key_storage, "os_credential_store");

  // While models load the backend answers with status, version and empty models only.
  const loading = parseHealth({ status: "loading", version: "0.1.0", models: {} });
  assert.equal(loading.status, "loading");
  assert.equal(loading.language, null);
  assert.equal(loading.hardware, null);
  assert.equal(loading.gpu, null);
  assert.equal(loading.profiles_key_storage, null);
  assert.equal(parseHealth("nope"), null);
});

test("settings expose the language and the brain kind", () => {
  assert.deepEqual(parseSettings({ mode: "smart", assistant_name: "Lena", language: "tr", budget_cap_usd: 2, tts_voice: "default", brain: "none" }), {
    language: "tr",
    brain: "none",
    assistant_name: "Lena",
    mode: "smart",
  });
  const odd = parseSettings({ language: "fr", brain: "ollama", mode: "loud" });
  assert.equal(odd.language, null);
  assert.equal(odd.brain, "ollama", "unknown brain kinds pass through");
  assert.equal(odd.mode, null);
  assert.equal(parseSettings([]), null);
});
