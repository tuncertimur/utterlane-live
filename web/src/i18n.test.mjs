import assert from "node:assert/strict";
import { isValidElement } from "react";
import test from "node:test";
import {
  LANGS,
  MESSAGES,
  browserLanguage,
  createTranslator,
  interpolate,
  isMessageKey,
  placeholders,
  resolveLanguage,
  translator,
} from "./i18n.ts";
import {
  brainLabel,
  captureError,
  identityKindLabel,
  keyStorageLabel,
  modelLabel,
  modelStatusLabel,
  nameStateLabel,
  noticeTitle,
  silentLine,
  speakerLabel,
  statusLabel,
} from "./labels.ts";
import { MODES, STATUS_STATES } from "./protocol.ts";

const en = translator("en");
const tr = translator("tr");

const forms = (entry) => (typeof entry === "string" ? [entry] : [entry.one, entry.other]);
const placeholderSet = (entry) => [...new Set(forms(entry).flatMap(placeholders))].sort();

// ---------------------------------------------------------------------------
// Dictionary completeness

test("every key exists in both languages, and nowhere else", () => {
  const keys = Object.keys(MESSAGES.en).sort();
  assert.ok(keys.length > 200, `expected the full UI dictionary, got ${keys.length} keys`);
  assert.deepEqual(LANGS.slice().sort(), ["en", "tr"]);
  for (const lang of LANGS) {
    const table = MESSAGES[lang];
    assert.deepEqual(Object.keys(table).sort(), keys, `${lang}: same key set as en`);
  }
});

test("every entry is non-empty text or a complete plural", () => {
  for (const lang of LANGS) {
    for (const [key, entry] of Object.entries(MESSAGES[lang])) {
      if (typeof entry === "string") {
        assert.ok(entry.trim().length > 0, `${lang} ${key}: empty`);
        continue;
      }
      assert.deepEqual(Object.keys(entry).sort(), ["one", "other"], `${lang} ${key}: plural needs one/other`);
      assert.ok(entry.one.trim() && entry.other.trim(), `${lang} ${key}: empty plural form`);
    }
  }
});

test("both languages use the same placeholders for every key", () => {
  for (const key of Object.keys(MESSAGES.en)) {
    assert.deepEqual(placeholderSet(MESSAGES.tr[key]), placeholderSet(MESSAGES.en[key]), `placeholders differ for ${key}`);
  }
});

test("plural entries are counted with {n}", () => {
  for (const lang of LANGS) {
    for (const [key, entry] of Object.entries(MESSAGES[lang])) {
      if (typeof entry !== "string") assert.ok(placeholderSet(entry).includes("n"), `${lang} ${key}: plural without {n}`);
    }
  }
});

test("Turkish is really translated: identical entries are only names, units and templates", () => {
  const sameOnPurpose = new Set([
    "app.documentTitle", // "{status} · Utterlane Live"
    "settings.micNative", // "16 kHz"
    "about.latencyValue", // "{ms} ms"
    "about.github",
    "brainKind.openai",
    "brainKind.openclaw",
    "brainKind.hermes",
    "brainKind.webhook",
  ]);
  const identical = Object.keys(MESSAGES.en).filter((key) => JSON.stringify(MESSAGES.en[key]) === JSON.stringify(MESSAGES.tr[key]));
  assert.deepEqual(identical.filter((key) => !sameOnPurpose.has(key)), []);
});

test("every protocol value the UI labels has a label in both languages", () => {
  const required = [
    ...STATUS_STATES.map((state) => `status.${state}`),
    "status.offline",
    "status.ready",
    ...MODES.flatMap((mode) => [`mode.${mode}`, `mode.${mode}.desc`]),
    ...["new_speaker", "named", "provisional", "confirmed", "corrected", "merged", "profile_saved", "consent_declined", "profile_recognized", "forgotten"].map(
      (kind) => `identity.${kind}`,
    ),
    ...["vad", "stt", "embedding", "tts", "llm"].map((model) => `model.${model}`),
    ...["ready", "loading", "error", "mock", "off"].map((status) => `modelStatus.${status}`),
    ...["not_addressed", "wake_word_missing", "listening_off", "speaker_not_allowed", "other"].map((reason) => `silentReason.${reason}`),
    ...["denied", "no_device", "busy", "insecure", "unsupported", "unknown"].flatMap((kind) => [`capture.${kind}.title`, `capture.${kind}.body`]),
    ...["openai", "openclaw", "hermes", "ollama", "lmstudio", "chat", "responses", "webhook", "mock", "none"].map((kind) => `brainKind.${kind}`),
    ...["info", "warn", "error"].map((level) => `notice.${level}`),
    ...["none", "provisional", "confirmed"].map((state) => `nameState.${state}`),
    ...["os_credential_store", "file"].map((storage) => `keyStorage.${storage}`),
    ...["short", "ambiguous", "overlap"].flatMap((state) => [`badge.${state}`, `badge.${state}.hint`]),
    ...["self_intro", "nomination", "correction", "consent_yes", "consent_no", "barge_in"].map((flag) => `badge.${flag}`),
  ];
  for (const key of required) assert.ok(isMessageKey(key), `missing ${key}`);
});

// ---------------------------------------------------------------------------
// Language selection

test("browser language: any Turkish locale → tr, everything else → en", () => {
  for (const tag of ["tr", "tr-TR", "TR", "tr-tr", "tr_TR", "tr-CY", " tr-TR "]) assert.equal(browserLanguage(tag), "tr", tag);
  for (const tag of ["en", "en-US", "en-GB", "de-DE", "az-AZ", "trk", "", "x"]) assert.equal(browserLanguage(tag), "en", tag);
  assert.equal(browserLanguage(undefined), "en");
  assert.equal(browserLanguage(null), "en");
});

test("before the first ready the browser decides; then the server's language wins", () => {
  assert.deepEqual(resolveLanguage({ browser: "tr" }), { lang: "tr", fromServer: false });
  assert.deepEqual(resolveLanguage({ ready: null, settings: null, browser: "en" }), { lang: "en", fromServer: false });
  // /api/settings may answer first (or alone, with an older backend).
  assert.deepEqual(resolveLanguage({ ready: null, settings: "tr", browser: "en" }), { lang: "tr", fromServer: true });
  // ready.language is the live source of truth.
  assert.deepEqual(resolveLanguage({ ready: "en", settings: "tr", browser: "tr" }), { lang: "en", fromServer: true });
  assert.deepEqual(resolveLanguage({ ready: "tr", browser: "en" }), { lang: "tr", fromServer: true });
});

// ---------------------------------------------------------------------------
// t(), plurals and rich text

test("t() interpolates and picks plural forms per language", () => {
  assert.equal(en.t("roster.turns", { n: 1 }), "1 turn");
  assert.equal(en.t("roster.turns", { n: 4 }), "4 turns");
  assert.equal(en.t("roster.turns", { n: 0 }), "0 turns");
  assert.equal(tr.t("roster.turns", { n: 1 }), "1 konuşma");
  assert.equal(tr.t("roster.turns", { n: 4 }), "4 konuşma");
  assert.equal(en.t("sidebar.present", { n: 1 }), "1 person");
  assert.equal(en.t("sidebar.present", { n: 3 }), "3 people");
  assert.equal(en.t("profiles.allCount", { n: 1 }), "the only saved profile");
  assert.equal(en.t("mode.wake.desc", { assistant: "Lena" }), "Answers only when someone says “Lena” or “Utterlane”.");
  assert.equal(tr.t("speaker.person", { n: 3 }), "3. kişi");
  assert.equal(en.t("speaker.person", { n: 3 }), "Person 3");
  assert.equal(tr.t("app.documentTitle", { status: "Dinliyorum" }), "Dinliyorum · Utterlane Live");
});

test("interpolate leaves unknown placeholders alone and ignores inherited names", () => {
  assert.equal(interpolate("{a} and {b}", { a: 1 }), "1 and {b}");
  assert.equal(interpolate("{constructor}", {}), "{constructor}");
  assert.equal(interpolate("no vars"), "no vars");
});

test("rich() puts nodes where each language wants them", () => {
  const enParts = en.rich("roster.forgetAsk", { name: "<Alex>" });
  assert.equal(enParts[0], "Forget ");
  assert.ok(isValidElement(enParts[1]));
  assert.equal(enParts[1].props.children, "<Alex>");
  assert.equal(enParts[2], "?");
  const trParts = tr.rich("roster.forgetAsk", { name: "<Ahmet>" });
  assert.ok(isValidElement(trParts[0]), "Turkish puts the name first");
  assert.equal(trParts[1], " unutulsun mu?");
  // Several slots, unrelated to vars; unused placeholders stay text.
  for (const i18n of [en, tr]) {
    const hint = i18n.rich("settings.languageHint", { cli: "CLI", env: "ENV", file: "FILE" });
    assert.equal(hint.filter(isValidElement).length, 3, `${i18n.lang}: three slots`);
    assert.equal(hint.filter((part) => typeof part === "string").join("").includes("{"), false);
  }
});

test("translators are cached per language and fall back to English for a bad language", () => {
  assert.equal(translator("tr"), translator("tr"));
  assert.notEqual(translator("tr"), translator("en"));
  const odd = createTranslator("xx");
  assert.equal(odd.t("status.listening"), "Listening");
  assert.equal(odd.locale, "en-US");
});

// ---------------------------------------------------------------------------
// labels.ts helpers on top of the dictionary

const spk = (extra) => ({ id: "spk-6", label: "", name: null, name_state: "none", ordinal: 6, persistent: false, present: true, turns: 1, color: 5, ...extra });

test("speaker labels: the server's localized label wins; fallbacks are localized", () => {
  assert.equal(speakerLabel(en, spk({ label: "3. kişi" })), "3. kişi", "never translate server labels");
  assert.equal(speakerLabel(en, spk()), "Person 6");
  assert.equal(speakerLabel(tr, spk()), "6. kişi");
  assert.equal(speakerLabel(en, spk({ id: "unknown", ordinal: null })), "Unclear");
  assert.equal(speakerLabel(tr, spk({ id: "unknown", ordinal: null })), "Belirsiz");
  assert.equal(speakerLabel(en, spk({ ordinal: null })), "Speaker");
});

test("label helpers cover unknown values without crashing", () => {
  assert.equal(statusLabel(en, "listening"), "Listening");
  assert.equal(statusLabel(tr, "offline"), "Bağlantı yok");
  assert.equal(statusLabel(en, "warming_up"), "Ready");
  assert.equal(silentLine(en, "Lena", "wake_word_missing"), "Lena stayed quiet — “Lena” wasn't said");
  assert.equal(silentLine(tr, "Lena", "not_addressed"), "Lena sessiz kaldı — kendisine hitap edilmedi");
  assert.equal(silentLine(en, "Lena", "line"), "Lena stayed quiet — no reply needed", "unknown reasons never hit other keys");
  assert.equal(identityKindLabel(en, "teleported"), "teleported");
  assert.equal(nameStateLabel(tr, "weird"), "isimsiz");
  assert.equal(modelLabel(en, "rerank"), "rerank");
  assert.equal(modelStatusLabel(tr, "off"), "kapalı");
  assert.equal(noticeTitle(en, "shout"), "Info");
  assert.equal(brainLabel(en, "ollama"), "Ollama (local)");
  assert.equal(brainLabel(en, "skynet"), "skynet");
  assert.equal(keyStorageLabel(tr, "file"), "Yalnızca sahibinin okuyabildiği anahtar dosyası");
  assert.equal(keyStorageLabel(en, "tpm"), "tpm");
  const insecure = captureError(en, "insecure", "http://127.0.0.1:8765");
  assert.match(insecure.body, /http:\/\/127\.0\.0\.1:8765\.$/);
  assert.equal(captureError(tr, "gremlins", "").title, "Ses başlatılamadı");
});
