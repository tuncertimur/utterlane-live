import assert from "node:assert/strict";
import test from "node:test";
import { parseServerMessage } from "./protocol.ts";
import {
  MAX_TOASTS,
  createInitialState,
  effectiveStatus,
  isListenOnly,
  lastReplyPlaying,
  liveReducer,
  modelProblems,
  sortRoster,
} from "./store.ts";

let clock = 1_000;

/** Feed raw server JSON through the real parser, then the reducer. */
function server(state, raw) {
  const parsed = parseServerMessage(raw);
  assert.equal(parsed.ok, true, parsed.ok ? "" : parsed.error);
  clock += 10;
  return liveReducer(state, { type: "server", message: parsed.message, at: clock });
}

function run(state, ...messages) {
  return messages.reduce(server, state);
}

const spk = (id, ordinal, color, extra = {}) => ({
  id,
  label: `${ordinal}. kişi`,
  name: null,
  name_state: "none",
  ordinal,
  persistent: false,
  present: true,
  turns: 1,
  color,
  ...extra,
});

const named = (base, name, state = "confirmed") => ({
  ...base,
  name,
  name_state: state,
  label: state === "provisional" ? `${name}?` : name,
});

const turn = (id, speaker, text, extra = {}) => ({
  id,
  speaker,
  text,
  started_at: 1_760_000_000,
  attribution: { state: "new", score: 0.6, margin: 0.2 },
  flags: [],
  ...extra,
});

const turnsOf = (state) => state.items.filter((item) => item.kind === "turn");
const kinds = (state) => state.items.map((item) => `${item.kind}:${item.id}`);

const READY = { type: "ready", session_id: "s-1", assistant_name: "Lena", mode: "smart", listening: true, models: {}, budget: { spent_usd: 0, cap_usd: 5 } };

test("a turn, then turn_update relabels it and records the previous speaker", () => {
  const one = spk("spk-1", 1, 0);
  let state = run(createInitialState(), READY, { type: "turn", turn: turn("t-1", one, "Merhaba, ben Tuncer.", { flags: ["self_intro"] }) });
  assert.equal(turnsOf(state)[0].turn.speaker.label, "1. kişi");
  assert.equal(turnsOf(state)[0].relabel, null);

  state = server(state, { type: "turn_update", turn_id: "t-1", speaker: named(one, "Tuncer") });
  const item = turnsOf(state)[0];
  assert.equal(item.turn.speaker.label, "Tuncer");
  assert.equal(item.relabel.from.label, "1. kişi");
  const firstSeq = item.relabel.seq;

  // Same label again: no new relabel animation.
  state = server(state, { type: "turn_update", turn_id: "t-1", speaker: { ...named(one, "Tuncer"), turns: 9 } });
  assert.equal(turnsOf(state)[0].relabel.seq, firstSeq);
  assert.equal(turnsOf(state)[0].turn.speaker.turns, 9);

  // Unknown turn id is ignored.
  const before = state;
  state = server(state, { type: "turn_update", turn_id: "t-404", speaker: named(one, "Tuncer") });
  assert.equal(state, before);
});

test("roster changes propagate provisional → confirmed names to earlier turns", () => {
  const four = spk("spk-4", 4, 3);
  let state = run(
    createInitialState(),
    READY,
    { type: "speakers", speakers: [named(four, "Ahmet", "provisional")] },
    { type: "turn", turn: turn("t-7", named(four, "Ahmet", "provisional"), "Selam, evet benim.") },
    { type: "turn", turn: turn("t-8", spk("unknown", 0, -1, { id: "unknown", label: "Belirsiz", ordinal: 0 }), "evet evet", { attribution: { state: "ambiguous" } }) },
  );
  assert.equal(turnsOf(state)[0].turn.speaker.label, "Ahmet?");
  state = server(state, { type: "speakers", speakers: [named(four, "Ahmet")] });
  const [ahmet, unknown] = turnsOf(state);
  assert.equal(ahmet.turn.speaker.label, "Ahmet");
  assert.equal(ahmet.turn.speaker.name_state, "confirmed");
  assert.equal(ahmet.relabel.from.label, "Ahmet?");
  assert.equal(unknown.turn.speaker.label, "Belirsiz");
  assert.equal(unknown.relabel, null);
  assert.equal(state.speakers.length, 1);
});

test("assistant deltas stream into one reply; assistant_end text is authoritative", () => {
  let state = run(
    createInitialState(),
    READY,
    { type: "turn", turn: turn("t-1", spk("spk-1", 1, 0), "Lena, merhaba!") },
    { type: "assistant_start", reply_id: "r-1", reply_seq: 1, in_reply_to: "t-1" },
    { type: "assistant_delta", reply_id: "r-1", text: "Merhaba " },
    { type: "assistant_delta", reply_id: "r-1", text: "Tuncer, " },
  );
  let reply = state.items.at(-1);
  assert.equal(reply.kind, "reply");
  assert.equal(reply.text, "Merhaba Tuncer, ");
  assert.equal(reply.streaming, true);
  assert.equal(reply.replySeq, 1);
  assert.equal(reply.inReplyTo, "t-1");

  state = server(state, { type: "assistant_end", reply_id: "r-1", text: "Merhaba Tuncer, hoş geldin.", interrupted: false, usage: { input_tokens: 90, output_tokens: 12, cost_usd: 0.0003 } });
  reply = state.items.at(-1);
  assert.equal(reply.text, "Merhaba Tuncer, hoş geldin.");
  assert.equal(reply.streaming, false);
  assert.equal(reply.interrupted, false);
  assert.equal(reply.usage.output_tokens, 12);

  // Late deltas after the end do not change the text.
  state = server(state, { type: "assistant_delta", reply_id: "r-1", text: "!!!" });
  assert.equal(state.items.at(-1).text, "Merhaba Tuncer, hoş geldin.");

  // A delta for a reply we never saw start still renders.
  state = server(state, { type: "assistant_delta", reply_id: "r-2", text: "Evet." });
  assert.equal(state.items.at(-1).id, "r-2");
  assert.equal(state.items.at(-1).text, "Evet.");
});

test("local playback events drive the speaking state", () => {
  let state = run(createInitialState(), READY, { type: "assistant_start", reply_id: "r-1", reply_seq: 1, in_reply_to: null });
  state = liveReducer(state, { type: "local_playback", replyId: "r-1", state: "started", at: 1 });
  state = server(state, { type: "status", state: "speaking" });
  assert.equal(lastReplyPlaying(state)?.id, "r-1");
  assert.equal(effectiveStatus(state), "speaking");
  state = liveReducer(state, { type: "local_playback", replyId: "r-1", state: "ended", at: 2 });
  assert.equal(state.items.at(-1).audio, "done");
  assert.equal(lastReplyPlaying(state), null);
  // The server may still say "speaking" until its reply task ends; nothing is audible.
  assert.equal(effectiveStatus(state), "listening");
  state = liveReducer(state, { type: "set_listening", enabled: false });
  assert.equal(effectiveStatus(state), "paused");
});

test("barge-in marks the reply interrupted and stops its audio", () => {
  let state = run(
    createInitialState(),
    READY,
    { type: "assistant_start", reply_id: "r-5", reply_seq: 5, in_reply_to: "t-9" },
    { type: "assistant_delta", reply_id: "r-5", text: "Elbette. Utterlane Live, odadaki" },
  );
  state = liveReducer(state, { type: "local_playback", replyId: "r-5", state: "started", at: 1 });
  state = run(
    state,
    { type: "stop_playback", reply_id: "r-5", reason: "barge_in" },
    { type: "turn", turn: turn("t-10", spk("spk-1", 1, 0), "Lena, bir saniye dur.", { flags: ["barge_in", "addressed_assistant"] }) },
    { type: "assistant_end", reply_id: "r-5", text: "Elbette. Utterlane Live, odadaki", interrupted: true },
  );
  const reply = state.items.find((item) => item.id === "r-5");
  assert.equal(reply.audio, "stopped");
  assert.equal(reply.interrupted, true);
  assert.equal(reply.streaming, false);
  assert.deepEqual(kinds(state), ["reply:r-5", "turn:t-10"]);
});

test("assistant_silent adds one subtle marker per turn", () => {
  let state = run(
    createInitialState(),
    READY,
    { type: "turn", turn: turn("t-3", spk("spk-3", 3, 2), "Toplantı kaçtaydı?") },
    { type: "assistant_silent", in_reply_to: "t-3", reason: "not_addressed" },
    { type: "assistant_silent", in_reply_to: "t-3", reason: "not_addressed" },
  );
  assert.deepEqual(kinds(state), ["turn:t-3", "silent:silent:t-3"]);
  assert.equal(state.items[1].reason, "not_addressed");
  state = server(state, { type: "assistant_silent", in_reply_to: null, reason: "listening_off" });
  assert.equal(state.items.length, 3);
});

test("socket loss finalises streaming replies; reconnect history merges without duplicates", () => {
  const one = spk("spk-1", 1, 0);
  const two = spk("spk-2", 2, 1);
  let state = run(
    createInitialState(),
    READY,
    { type: "turn", turn: turn("t-1", one, "Merhaba.") },
    { type: "assistant_start", reply_id: "r-1", reply_seq: 1, in_reply_to: "t-1" },
    { type: "assistant_delta", reply_id: "r-1", text: "Merhaba!" },
    { type: "turn", turn: turn("t-3", two, "Ben de buradayım.") },
  );
  state = liveReducer(state, { type: "connection", state: "open", at: 1 });
  state = liveReducer(state, { type: "connection", state: "reconnecting", retryInMs: 1000, at: 2 });
  const orphan = state.items.find((item) => item.id === "r-1");
  assert.equal(orphan.streaming, false);
  assert.equal(orphan.interrupted, true);
  assert.equal(state.connectedOnce, true);
  assert.equal(state.retryInMs, 1000);

  state = liveReducer(state, { type: "connection", state: "open", at: 3 });
  const history = {
    type: "history",
    turns: [
      turn("t-1", named(one, "Tuncer"), "Merhaba."),
      turn("t-2", one, "Bunu kaçırmıştın."),
      turn("t-3", two, "Ben de buradayım."),
      turn("t-4", two, "Yeni bir şey."),
    ],
  };
  state = run(state, READY, history);
  assert.deepEqual(kinds(state), ["turn:t-1", "reply:r-1", "turn:t-2", "turn:t-3", "turn:t-4"]);
  assert.equal(state.items[0].turn.speaker.label, "Tuncer");
  assert.equal(state.items[0].relabel.from.label, "1. kişi", "a relabel missed while offline is still shown");

  // Replaying the same history is idempotent.
  const again = server(state, history);
  assert.deepEqual(kinds(again), kinds(state));
  assert.equal(again.items[0].relabel.seq, state.items[0].relabel.seq);
});

test("history is authoritative: reset clears, the window drops stale turns, older turns stay", () => {
  const one = spk("spk-1", 1, 0);
  let state = run(
    createInitialState(),
    READY,
    { type: "turn", turn: turn("t-1", one, "Eski konuşma.") },
    { type: "assistant_silent", in_reply_to: "t-1", reason: "not_addressed" },
    { type: "turn", turn: turn("t-2", one, "Pencerede.") },
    { type: "turn", turn: turn("t-3", one, "Sunucuda yok.") },
    { type: "turn", turn: turn("t-4", one, "Son.") },
  );
  // Server window starts at t-2 (older turns trimmed) and no longer has t-3.
  state = server(state, { type: "history", turns: [turn("t-2", one, "Pencerede."), turn("t-4", one, "Son.")] });
  assert.deepEqual(kinds(state), ["turn:t-1", "silent:silent:t-1", "turn:t-2", "turn:t-4"]);

  // reset_session → the server sends history with no turns.
  state = server(state, { type: "history", turns: [] });
  assert.deepEqual(state.items, []);
});

test("history on a fresh page places proposed `replies` after their turns", () => {
  const one = spk("spk-1", 1, 0);
  const state = run(createInitialState(), READY, {
    type: "history",
    turns: [turn("t-1", one, "Merhaba."), turn("t-2", one, "Nasılsın?")],
    replies: [
      { reply_id: "r-2", in_reply_to: "t-2", text: "İyiyim.", interrupted: false },
      { reply_id: "r-1", in_reply_to: "t-1", text: "Merhaba!", interrupted: true },
    ],
  });
  assert.deepEqual(kinds(state), ["turn:t-1", "reply:r-1", "turn:t-2", "reply:r-2"]);
  assert.equal(state.items[1].interrupted, true);
  assert.equal(state.items[1].streaming, false);
});

test("a new session id clears the previous conversation", () => {
  let state = run(createInitialState(), READY, { type: "turn", turn: turn("t-1", spk("spk-1", 1, 0), "Eski.") }, { type: "identity_event", kind: "new_speaker", speaker_id: "spk-1", message: "Yeni ses." });
  assert.equal(state.items.length, 1);
  state = server(state, { ...READY, session_id: "s-2" });
  assert.equal(state.items.length, 0);
  assert.equal(state.identityLog.length, 0);
  assert.equal(state.sessionId, "s-2");
});

test("status, level, usage, identity log and notices", () => {
  let state = run(createInitialState(), READY);
  assert.equal(state.status.state, "listening");
  state = server(state, { type: "status", state: "paused" });
  assert.equal(state.listening, false);
  state = server(state, { type: "status", state: "listening" });
  assert.equal(state.listening, true);

  const before = server(state, { type: "level", rms: 0.2, speech: false });
  assert.equal(before, state, "level without a speech change must not re-render");
  state = server(state, { type: "level", rms: 0.5, speech: true });
  assert.equal(state.speech, true);

  state = server(state, { type: "usage", spent_usd: 0.42, cap_usd: 5, calls: 7 });
  assert.deepEqual(state.budget, { spent_usd: 0.42, cap_usd: 5, calls: 7 });

  state = run(
    state,
    { type: "identity_event", kind: "new_speaker", speaker_id: "spk-1", message: "Yeni bir ses duyuldu: 1. kişi." },
    { type: "identity_event", kind: "profile_saved", speaker_id: "spk-1", name: "Tuncer", message: "Tuncer'in ses profili kaydedildi." },
  );
  assert.deepEqual(state.identityLog.map((entry) => entry.kind), ["profile_saved", "new_speaker"]);

  for (let index = 0; index < MAX_TOASTS + 2; index += 1) {
    state = server(state, { type: "notice", level: "warn", code: `c${index}`, message: `Uyarı ${index}` });
  }
  assert.equal(state.toasts.length, MAX_TOASTS);
  state = server(state, { type: "notice", level: "warn", code: `c${MAX_TOASTS + 1}`, message: `Uyarı ${MAX_TOASTS + 1}` });
  assert.equal(state.toasts.length, MAX_TOASTS, "identical notice replaces instead of stacking");
  state = liveReducer(state, { type: "dismiss_toast", id: state.toasts[0].id });
  assert.equal(state.toasts.length, MAX_TOASTS - 1);

  const unknown = server(state, { type: "brand_new_feature", x: 1 });
  assert.equal(unknown, state);
});

test("ready stores the server language; a ready without one keeps the last known", () => {
  let state = createInitialState();
  assert.equal(state.language, null, "unknown until the server says");
  state = server(state, { ...READY, language: "en" });
  assert.equal(state.language, "en");
  state = server(state, { ...READY, language: "tr" });
  assert.equal(state.language, "tr", "a restarted server may switch language");
  state = server(state, READY);
  assert.equal(state.language, "tr");
});

test("listen-only (no_brain) shows one system line per session, not one per turn", () => {
  const one = spk("spk-1", 1, 0);
  let state = run(
    createInitialState(),
    READY,
    { type: "turn", turn: turn("t-1", one, "Hello?") },
    { type: "assistant_silent", in_reply_to: "t-1", reason: "no_brain" },
    { type: "turn", turn: turn("t-2", one, "Anyone there?") },
    { type: "assistant_silent", in_reply_to: "t-2", reason: "no_brain" },
    { type: "assistant_silent", in_reply_to: "t-2", reason: "no_brain" },
  );
  assert.deepEqual(
    state.items.map((item) => (item.kind === "silent" ? `silent:${item.reason}` : `${item.kind}:${item.id}`)),
    ["turn:t-1", "silent:no_brain", "turn:t-2"],
  );
  assert.equal(state.listenOnlyNoted, true);

  // Other silences are unaffected.
  state = server(state, { type: "assistant_silent", in_reply_to: "t-2", reason: "not_addressed" });
  assert.equal(state.items.at(-1).reason, "not_addressed");

  // A reconnect to the same session keeps the line and does not repeat it.
  state = server(state, READY);
  state = server(state, { type: "assistant_silent", in_reply_to: "t-2", reason: "no_brain" });
  assert.equal(state.items.filter((item) => item.reason === "no_brain").length, 1);

  // reset_session (empty history) starts a new conversation: the line may appear again.
  state = server(state, { type: "history", turns: [] });
  assert.equal(state.listenOnlyNoted, false);
  state = run(state, { type: "turn", turn: turn("t-3", one, "Again.") }, { type: "assistant_silent", in_reply_to: "t-3", reason: "no_brain" });
  assert.equal(state.items.filter((item) => item.reason === "no_brain").length, 1);

  // So does a new server session.
  state = server(state, { ...READY, session_id: "s-9" });
  assert.equal(state.listenOnlyNoted, false);
  assert.equal(state.items.length, 0);
});

test("listen-only is known from the llm status, the brain setting or a no_brain silence", () => {
  let state = createInitialState();
  assert.equal(isListenOnly(state), false);
  assert.equal(isListenOnly(state, "none"), true);
  assert.equal(isListenOnly(state, "openai"), false);
  state = server(state, { ...READY, models: { llm: { id: "none (OPENAI_API_KEY not set)", status: "off" } } });
  assert.equal(isListenOnly(state, "openai"), true, "brain=openai without a key is still listen-only");
  assert.deepEqual(modelProblems(state.models), [], "a deliberately disabled brain is not a model error");
  state = server(createInitialState(), { type: "assistant_silent", in_reply_to: null, reason: "no_brain" });
  assert.equal(isListenOnly(state), true);
});

test("local toasts carry a dictionary key; server notices keep their localized text", () => {
  let state = liveReducer(createInitialState(), { type: "toast", level: "warn", code: "mic_lost", key: "toast.micLost", at: 1 });
  assert.equal(state.toasts[0].key, "toast.micLost");
  assert.equal(state.toasts[0].message, "");
  state = liveReducer(state, { type: "toast", level: "warn", code: "mic_lost", key: "toast.micLost", at: 2 });
  assert.equal(state.toasts.length, 1, "the same keyed toast collapses");
  state = server(state, { type: "notice", level: "info", code: "session_reset", message: "Session reset. Saved voices are kept." });
  assert.equal(state.toasts.at(-1).key, null);
  assert.equal(state.toasts.at(-1).message, "Session reset. Saved voices are kept.");
});

test("model problems and roster ordering selectors", () => {
  assert.deepEqual(
    modelProblems({ stt: { id: "a", status: "ready", detail: null }, tts: { id: "b", status: "error", detail: "GPU yok" } }),
    [{ key: "tts", detail: "GPU yok" }],
  );
  const roster = sortRoster([
    spk("spk-3", 3, 2, { present: false }),
    spk("spk-2", 2, 1),
    { ...spk("unknown", 0, -1), id: "unknown" },
    spk("spk-1", 1, 0),
  ]);
  assert.deepEqual(roster.map((speaker) => speaker.id), ["spk-1", "spk-2", "spk-3"]);
});
