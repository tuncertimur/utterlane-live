// UI copy in English and Turkish, the language-selection rules, and a tiny React API.
//
// Everything the UI itself says lives in `MESSAGES`. Text that comes from the server
// (speaker labels such as "Person 3" / "3. kişi", identity-event messages, notices, model
// details, the hardware summary) is already localized by the backend and is shown as-is.
//
// `MESSAGES.tr` must define exactly the keys of `MESSAGES.en`; the type checker enforces
// it and i18n.test.mjs checks it again at runtime, together with matching placeholders.
//
// Placeholders: `{name}` is replaced from `vars`. A plural entry `{ one, other }` is picked
// with Intl.PluralRules from `vars.n`. `rich()` swaps placeholders for React nodes so a
// view can wrap part of a sentence in <strong> or <code> whatever the word order.

import { createContext, createElement, Fragment, useContext, type ReactNode } from "react";
import type { Language } from "./protocol.ts";

export type Lang = Language;
export const LANGS: readonly Lang[] = ["en", "tr"];
/** Used for anything missing at runtime, and before React renders a provider. */
export const FALLBACK_LANG: Lang = "en";

export interface Plural {
  one: string;
  other: string;
}
export type Entry = string | Plural;
export type Vars = Record<string, string | number>;

const en = {
  // document title and app chrome
  "app.documentTitle": "{status} · Utterlane Live",
  "app.replaced": "Utterlane Live was opened in another tab; this tab is on standby.",
  "app.continueHere": "Continue here",
  "app.reconnecting": "Lost connection to the server. Reconnecting…",
  "app.reconnectingIn": "Lost connection to the server. Reconnecting in {s} s…",
  "app.retryNow": "Retry now",

  // connection state next to the wordmark
  "conn.open": "Connected",
  "conn.connecting": "Connecting…",
  "conn.reconnecting": "Reconnecting…",
  "conn.replaced": "Open in another tab",
  "conn.closed": "Disconnected",

  // status pill
  "status.listening": "Listening",
  "status.hearing": "Hearing speech",
  "status.thinking": "Thinking",
  "status.speaking": "Speaking",
  "status.paused": "Paused",
  "status.loading": "Loading",
  "status.ready": "Ready",
  "status.offline": "Offline",

  // response modes
  "mode.label": "Response mode",
  "mode.smart": "Smart",
  "mode.always": "Always reply",
  "mode.wake": "Wake word",
  "mode.smart.desc":
    "With one person in the room it answers everything; with several people it speaks only when someone addresses it.",
  "mode.always.desc": "Answers every turn. Best when you're on your own.",
  "mode.wake.desc": "Answers only when someone says “{assistant}” or “Utterlane”.",

  // footer line under the conversation
  "dock.smartGroup": "{n} people here; {assistant} speaks only when addressed",
  "dock.smartSolo": "{assistant} answers everything",
  "dock.always": "answers every turn",
  "dock.wake": "answers when you say “{assistant}”",
  "dock.listenOnly": "Listen-only",
  "dock.listenOnlyDetail": "transcribing and labelling speakers; no AI brain configured",
  "dock.noMic": "No microphone",
  "dock.micOff": "Microphone off",

  // top bar
  "topbar.modelError": "Model error — {problems}",
  "topbar.modelFailed": "failed to load",
  "topbar.stop": "Stop",
  "topbar.stopTitle": "Stop speaking (Esc)",
  "topbar.people": "People in the room",
  "topbar.settings": "Settings",
  "mic.turnOn": "Turn the microphone on",
  "mic.turnOff": "Turn the microphone off",
  "mic.resumeTitle": "Resume listening (M)",
  "mic.pauseTitle": "Pause listening (M)",
  "mic.default": "Default microphone",
  "mic.numbered": "Microphone {n}",
  "mic.generic": "Microphone",
  "theme.toLight": "Switch to the light theme",
  "theme.toDark": "Switch to the dark theme",
  "theme.lightTitle": "Light theme",
  "theme.darkTitle": "Dark theme",

  // speakers (fallbacks only: the server normally sends the label)
  "speaker.person": "Person {n}",
  "speaker.unknown": "Unclear",
  "speaker.generic": "Speaker",
  "chip.unknown": "This voice didn't match any speaker",
  "chip.provisional": "Provisional name: {name}",
  "chip.ordinal": "Hasn't introduced themselves yet",
  "chip.saved": "Voice profile saved on this computer",
  "chip.named": "Introduced themselves",
  "chip.savedIcon": "saved",

  // conversation timeline
  "timeline.label": "Conversation",
  "timeline.newMessages": { one: "{n} new message", other: "{n} new messages" },
  "timeline.jumpLatest": "Jump to latest",
  "turn.match": "Match {score}",
  "turn.matchMargin": "Match {score} · margin {margin}",
  "turn.relabeled": "This turn was relabeled",
  "turn.previously": "was {label}",
  "turn.addressed": "Addressed to the assistant",
  "reply.tokens": "{input} → {output} tokens",
  "reply.cut": "cut off",
  "reply.cutTitle": "The reply was cut off",
  "reply.typing": "{assistant} is writing",
  "silent.line": "{assistant} stayed quiet — {reason}",
  "silent.hide": "Hide quiet notes",
  "silentReason.not_addressed": "not addressed",
  "silentReason.wake_word_missing": "“{assistant}” wasn't said",
  "silentReason.listening_off": "listening is off",
  "silentReason.speaker_not_allowed": "only recognized speakers get replies",
  "silentReason.other": "no reply needed",
  "silent.listenOnly": "Listen-only mode — no AI brain configured",
  "silent.listenOnlyAction": "How to add one",
  "empty.title": "Start talking",
  "empty.body":
    "{assistant} is listening. Introduce yourself — “I'm Alex” — and {assistant} will remember your voice; voices that never introduce themselves get labels like “Person 3”.",
  "empty.hint1": "“Hi, I'm Sam.”",
  "empty.hint2": "“{assistant}, what's on today?”",
  "empty.hint3": "“Alex, go ahead.”",

  // badges on a turn
  "badge.short": "guessed",
  "badge.short.hint": "Very short audio; attributed to the previous speaker as a guess.",
  "badge.ambiguous": "unclear",
  "badge.ambiguous.hint": "The voice didn't reliably match any speaker.",
  "badge.overlap": "overlap",
  "badge.overlap.hint": "Several people spoke at once.",
  "badge.self_intro": "introduced",
  "badge.nomination": "gave the floor",
  "badge.correction": "correction",
  "badge.consent_yes": "consented",
  "badge.consent_no": "declined",
  "badge.barge_in": "interrupted",

  // identity events (kind headings; the message itself comes from the server)
  "identity.new_speaker": "New voice",
  "identity.named": "Named",
  "identity.provisional": "Provisional name",
  "identity.confirmed": "Confirmed",
  "identity.corrected": "Corrected",
  "identity.merged": "Merged",
  "identity.profile_saved": "Profile saved",
  "identity.consent_declined": "Consent declined",
  "identity.profile_recognized": "Profile recognized",
  "identity.forgotten": "Forgotten",

  // room sidebar
  "sidebar.label": "Room",
  "sidebar.title": "In the room",
  "sidebar.present": { one: "{n} person", other: "{n} people" },
  "sidebar.emptyCount": "empty",
  "sidebar.close": "Close",
  "sidebar.noSpeakers": "Nobody has spoken yet. Everyone who speaks shows up here.",
  "sidebar.events": "Identity events",
  "sidebar.noEvents": "Introductions, consents and corrections show up here.",
  "nameState.confirmed": "confirmed",
  "nameState.provisional": "provisional",
  "nameState.none": "unnamed",
  "roster.turns": { one: "{n} turn", other: "{n} turns" },
  "roster.away": "away",
  "roster.savedTitle": "Voice profile stored encrypted on this computer",
  "roster.options": "Options for {name}",
  "roster.rename": "Rename",
  "roster.forget": "Forget",
  "roster.newName": "New name",
  "roster.namePlaceholder": "Name",
  "roster.save": "Save",
  "roster.forgetAsk": "Forget {name}?",
  "roster.forgetProfileToo": "Their saved voice profile is deleted too.",
  "usage.title": "Language model usage",
  "usage.aria": "Budget used",
  "common.cancel": "Cancel",
  "common.refresh": "Refresh",
  "common.loading": "Loading…",

  // start card
  "start.serverOpen": "Server ready",
  "start.serverRetrying": "Can't reach the server, retrying…",
  "start.serverReplaced": "The session is open in another tab",
  "start.serverConnecting": "Connecting to the server…",
  "start.title": "A hands-free assistant that knows who's talking",
  "start.lead":
    "Just start talking. Introduce yourself — “I'm Alex” — and I'll remember your voice; voices that never introduce themselves get labels like “Person 3”.",
  "start.privacy":
    "Audio never leaves this computer. Voiceprints are saved only with your spoken consent, and encrypted; only the text of the conversation goes to the language model.",
  "start.opening": "Opening the microphone…",
  "start.retry": "Try again",
  "start.start": "Start",
  "start.withoutMic": "Continue without a microphone",
  "start.permGranted": "Microphone access is already allowed.",
  "start.permDenied": "Microphone access is blocked.",
  "start.permPrompt": "When you press Start, the browser asks for the microphone; then {assistant} starts listening.",

  // microphone errors
  "capture.denied.title": "Microphone access was denied",
  "capture.denied.body": "Click the lock icon in the address bar, allow the microphone, then try again.",
  "capture.no_device.title": "No microphone found",
  "capture.no_device.body": "Connect a microphone or enable one in your system settings, then try again.",
  "capture.busy.title": "Microphone unavailable",
  "capture.busy.body": "Another app may be using the microphone. Close it and try again.",
  "capture.insecure.title": "A secure connection is required",
  "capture.insecure.body": "Browsers only open the microphone over https or on 127.0.0.1. Open this page at {url}.",
  "capture.unsupported.title": "Browser not supported",
  "capture.unsupported.body":
    "This browser lacks the audio features Utterlane Live needs. Try a current Chrome, Edge or Firefox.",
  "capture.unknown.title": "Couldn't start audio",
  "capture.unknown.body": "Something unexpected went wrong. Reload the page and try again.",

  // toasts
  "notice.info": "Info",
  "notice.warn": "Warning",
  "notice.error": "Error",
  "toast.dismiss": "Dismiss",
  "toast.micLost": "The microphone was disconnected; switching to the default microphone.",

  // settings drawer
  "settings.title": "Settings",
  "settings.close": "Close settings",
  "settings.mic": "Microphone",
  "settings.inputDevice": "Input device",
  "settings.micActive": "Active: {mic} · {rate} · echo cancellation and noise suppression on",
  "settings.micResampled": "{khz} kHz → 16 kHz (filtered resampling)",
  "settings.micNative": "16 kHz",
  "settings.micListenOnly": "You're in no-microphone mode. To use the microphone, reload the page and press Start.",
  "settings.micHint": "Microphone names appear after you press Start and allow access.",
  "settings.language": "Language",
  "settings.languageName": "English",
  "settings.languageServer": "set by the server",
  "settings.languageBrowser": "your browser's language, until the server connects",
  "settings.languageHint": "To change it, start Utterlane Live with {cli} or set {env} in {file}.",
  "settings.appearance": "Appearance",
  "settings.theme": "Theme",
  "settings.themeSystem": "System",
  "settings.themeDark": "Dark",
  "settings.themeLight": "Light",
  "settings.showSilent": "Show when {assistant} stays quiet",

  "profiles.title": "Saved voice profiles",
  "profiles.hint":
    "Voiceprints of people who gave spoken consent. After you delete a profile, that person has to introduce themselves again next time.",
  "profiles.error": "Couldn't load the profiles. Is the server running?",
  "profiles.none": "No saved profiles.",
  "profiles.unnamed": "Unnamed",
  "profiles.samples": { one: "{n} sample", other: "{n} samples" },
  "profiles.delete": "Delete",
  "profiles.deleteOne": "Delete {name}'s profile",
  "profiles.deleteAll": "Delete all profiles",
  "profiles.deleteAllAsk": "Delete all profiles?",
  "profiles.deleteAllConfirm": "Permanently delete {count}? This can't be undone.",
  "profiles.allCount": { one: "the only saved profile", other: "all {n} profiles" },
  "profiles.deleteAllButton": "Delete all",
  "profiles.deleted": "Voice profile deleted.",
  "profiles.deletedAll": "All voice profiles deleted.",
  "profiles.deleteFailed": "Couldn't delete.",

  "budget.title": "Budget",
  "budget.unlimited": "no cap",
  "budget.hint": {
    one: "{n} language-model call this session. When the cap is reached, {assistant} stops replying.",
    other: "{n} language-model calls this session. When the cap is reached, {assistant} stops replying.",
  },

  // AI brain
  "brain.title": "AI brain",
  "brain.listenOnlyTitle": "Listen-only mode",
  "brain.listenOnlyBody":
    "No AI brain is configured, so {assistant} transcribes the conversation and labels the speakers but never replies.",
  "brain.howTo":
    "To add one, put {openaiKey} in {file}, run a local model through Ollama with {chat}, or connect OpenClaw or Hermes Agent. {docs} explains each option.",
  "brain.restart": "Restart Utterlane Live after changing it.",
  "brain.off": "None — listen-only",
  "brainKind.openai": "OpenAI",
  "brainKind.openclaw": "OpenClaw",
  "brainKind.hermes": "Hermes Agent",
  "brainKind.ollama": "Ollama (local)",
  "brainKind.lmstudio": "LM Studio (local)",
  "brainKind.chat": "OpenAI-compatible server",
  "brainKind.responses": "OpenAI-compatible server (Responses API)",
  "brainKind.webhook": "Webhook",
  "brainKind.mock": "Placeholder replies (development)",
  "brainKind.none": "None",

  // about / models
  "about.title": "About",
  "about.serverLoading": "The server is still loading its models…",
  "about.error": "Couldn't load the system information. Is the server running?",
  "about.waiting": "Waiting for the server…",
  "about.version": "Version",
  "about.hardware": "Hardware",
  "about.keyStorage": "Profile key",
  "about.latency": "Server latency",
  "about.latencyValue": "{ms} ms",
  "about.github": "GitHub",
  "about.githubTitle": "Source code on GitHub (opens in a new tab)",
  "model.stt": "Speech to text",
  "model.tts": "Voice",
  "model.llm": "AI brain",
  "model.embedding": "Voice ID",
  "model.vad": "Voice activity",
  "modelStatus.ready": "ready",
  "modelStatus.loading": "loading",
  "modelStatus.error": "error",
  "modelStatus.mock": "mock",
  "modelStatus.off": "off",
  "keyStorage.os_credential_store": "OS credential store",
  "keyStorage.file": "Owner-only key file",

  // privacy
  "privacy.title": "Privacy",
  "privacy.local": "Voiceprints (the numeric summaries that tell speakers apart) never leave this computer.",
  "privacy.consent": "A voiceprint is saved only after that person agrees out loud.",
  "privacy.encryptedOs": "Saved voiceprints are encrypted with a key kept in your operating system's credential store.",
  "privacy.encryptedFile":
    "Saved voiceprints are encrypted with a key kept in an owner-only file on this computer (no OS credential store was found).",
  "privacy.textOnly":
    "Audio recordings are never sent anywhere; only the transcribed text of the conversation goes to the language model.",
  "privacy.label": "A voice match is only a label; it never unlocks permissions or personal information.",

  // session
  "session.title": "Session",
  "session.reset": "Reset session",
  "session.resetAsk": "Reset the session?",
  "session.resetBody": "This clears the conversation and this session's speakers. Saved voice profiles stay.",
  "session.resetConfirm": "Reset",
} satisfies Record<string, Entry>;

/** Every UI string has one of these keys. */
export type MessageKey = keyof typeof en;

const tr: Record<MessageKey, Entry> = {
  "app.documentTitle": "{status} · Utterlane Live",
  "app.replaced": "Utterlane Live başka bir sekmede açıldı; bu sekme beklemede.",
  "app.continueHere": "Burada devam et",
  "app.reconnecting": "Sunucu bağlantısı koptu. Yeniden bağlanılıyor…",
  "app.reconnectingIn": "Sunucu bağlantısı koptu. Yeniden bağlanılıyor ({s} sn)…",
  "app.retryNow": "Şimdi dene",

  "conn.open": "Bağlı",
  "conn.connecting": "Bağlanıyor…",
  "conn.reconnecting": "Yeniden bağlanıyor…",
  "conn.replaced": "Başka sekmede açık",
  "conn.closed": "Bağlantı yok",

  "status.listening": "Dinliyorum",
  "status.hearing": "Duyuyorum",
  "status.thinking": "Düşünüyor",
  "status.speaking": "Konuşuyor",
  "status.paused": "Duraklatıldı",
  "status.loading": "Yükleniyor",
  "status.ready": "Hazır",
  "status.offline": "Bağlantı yok",

  "mode.label": "Yanıt modu",
  "mode.smart": "Akıllı",
  "mode.always": "Her şeye cevap",
  "mode.wake": "Sadece hitap",
  "mode.smart.desc":
    "Odada tek kişi varsa her söyleneni yanıtlar; birden fazla kişi varsa yalnızca kendisine hitap edildiğinde konuşur.",
  "mode.always.desc": "Her konuşmaya yanıt verir. Tek kişilik kullanım için uygundur.",
  "mode.wake.desc": "Yalnızca “{assistant}” ya da “Utterlane” dendiğinde yanıt verir.",

  "dock.smartGroup": "{n} kişi var; {assistant} yalnızca hitap edilince konuşur",
  "dock.smartSolo": "{assistant} her söyleneni yanıtlar",
  "dock.always": "her konuşmaya yanıt verir",
  "dock.wake": "“{assistant}” deyince yanıtlar",
  "dock.listenOnly": "Yalnızca dinleme",
  "dock.listenOnlyDetail": "konuşmalar yazıya dökülüyor, konuşmacılar etiketleniyor; yapay zekâ beyni yapılandırılmamış",
  "dock.noMic": "Mikrofonsuz mod",
  "dock.micOff": "Mikrofon kapalı",

  "topbar.modelError": "Model hatası — {problems}",
  "topbar.modelFailed": "yüklenemedi",
  "topbar.stop": "Durdur",
  "topbar.stopTitle": "Konuşmayı durdur (Esc)",
  "topbar.people": "Odadakiler",
  "topbar.settings": "Ayarlar",
  "mic.turnOn": "Mikrofonu aç",
  "mic.turnOff": "Mikrofonu kapat",
  "mic.resumeTitle": "Dinlemeyi sürdür (M)",
  "mic.pauseTitle": "Dinlemeyi duraklat (M)",
  "mic.default": "Varsayılan mikrofon",
  "mic.numbered": "Mikrofon {n}",
  "mic.generic": "Mikrofon",
  "theme.toLight": "Açık temaya geç",
  "theme.toDark": "Koyu temaya geç",
  "theme.lightTitle": "Açık tema",
  "theme.darkTitle": "Koyu tema",

  "speaker.person": "{n}. kişi",
  "speaker.unknown": "Belirsiz",
  "speaker.generic": "Konuşmacı",
  "chip.unknown": "Ses hiçbir konuşmacıyla eşleşmedi",
  "chip.provisional": "Tahmini isim: {name}",
  "chip.ordinal": "Henüz tanıtılmamış konuşmacı",
  "chip.saved": "Ses profili bu bilgisayarda kayıtlı",
  "chip.named": "Kendini tanıttı",
  "chip.savedIcon": "kayıtlı",

  "timeline.label": "Konuşma",
  "timeline.newMessages": "{n} yeni mesaj",
  "timeline.jumpLatest": "En sona git",
  "turn.match": "Eşleşme {score}",
  "turn.matchMargin": "Eşleşme {score} · fark {margin}",
  "turn.relabeled": "Bu konuşma yeniden etiketlendi",
  "turn.previously": "önce {label}",
  "turn.addressed": "Asistana hitap edildi",
  "reply.tokens": "{input} → {output} token",
  "reply.cut": "kesildi",
  "reply.cutTitle": "Yanıt yarıda kesildi",
  "reply.typing": "{assistant} yazıyor",
  "silent.line": "{assistant} sessiz kaldı — {reason}",
  "silent.hide": "Sessiz notlarını gizle",
  "silentReason.not_addressed": "kendisine hitap edilmedi",
  "silentReason.wake_word_missing": "“{assistant}” denmedi",
  "silentReason.listening_off": "dinleme kapalı",
  "silentReason.speaker_not_allowed": "yalnızca tanınan konuşmacılara yanıt verir",
  "silentReason.other": "yanıt gerekmedi",
  "silent.listenOnly": "Yalnızca dinleme modu — yapay zekâ beyni yapılandırılmamış",
  "silent.listenOnlyAction": "Nasıl eklenir?",
  "empty.title": "Konuşmaya başlayın",
  "empty.body":
    "{assistant} sizi dinliyor. Kendinizi “Ben Tuncer” diye tanıtırsanız sizi tanır; tanıtılmayan sesleri “3. kişi” gibi etiketler.",
  "empty.hint1": "“Merhaba, ben Zümra.”",
  "empty.hint2": "“{assistant}, bugün neler yapacağız?”",
  "empty.hint3": "“Ahmet, sen konuş.”",

  "badge.short": "tahmini",
  "badge.short.hint": "Çok kısa bir ses; önceki konuşmacıya tahmini olarak atandı.",
  "badge.ambiguous": "belirsiz",
  "badge.ambiguous.hint": "Ses hiçbir konuşmacıyla güvenilir biçimde eşleşmedi.",
  "badge.overlap": "üst üste",
  "badge.overlap.hint": "Birden fazla kişi aynı anda konuştu.",
  "badge.self_intro": "kendini tanıttı",
  "badge.nomination": "söz verdi",
  "badge.correction": "düzeltme",
  "badge.consent_yes": "onay verdi",
  "badge.consent_no": "onay vermedi",
  "badge.barge_in": "araya girdi",

  "identity.new_speaker": "Yeni ses",
  "identity.named": "Adlandırıldı",
  "identity.provisional": "Tahmini isim",
  "identity.confirmed": "Doğrulandı",
  "identity.corrected": "Düzeltildi",
  "identity.merged": "Birleştirildi",
  "identity.profile_saved": "Profil kaydedildi",
  "identity.consent_declined": "Onay verilmedi",
  "identity.profile_recognized": "Profil tanındı",
  "identity.forgotten": "Unutuldu",

  "sidebar.label": "Oda",
  "sidebar.title": "Odadakiler",
  "sidebar.present": "{n} kişi",
  "sidebar.emptyCount": "boş",
  "sidebar.close": "Kapat",
  "sidebar.noSpeakers": "Henüz kimse konuşmadı. Konuşan herkes burada görünür.",
  "sidebar.events": "Kimlik olayları",
  "sidebar.noEvents": "Tanıtmalar, onaylar ve düzeltmeler burada listelenir.",
  "nameState.confirmed": "kesin",
  "nameState.provisional": "tahmini",
  "nameState.none": "isimsiz",
  "roster.turns": "{n} konuşma",
  "roster.away": "uzakta",
  "roster.savedTitle": "Ses profili bu bilgisayarda şifreli olarak kayıtlı",
  "roster.options": "{name} için seçenekler",
  "roster.rename": "Yeniden adlandır",
  "roster.forget": "Unut",
  "roster.newName": "Yeni isim",
  "roster.namePlaceholder": "İsim",
  "roster.save": "Kaydet",
  "roster.forgetAsk": "{name} unutulsun mu?",
  "roster.forgetProfileToo": "Kayıtlı ses profili de silinir.",
  "usage.title": "Dil modeli kullanımı",
  "usage.aria": "Bütçe kullanımı",
  "common.cancel": "Vazgeç",
  "common.refresh": "Yenile",
  "common.loading": "Yükleniyor…",

  "start.serverOpen": "Sunucu hazır",
  "start.serverRetrying": "Sunucuya ulaşılamıyor, yeniden deneniyor…",
  "start.serverReplaced": "Oturum başka bir sekmede açık",
  "start.serverConnecting": "Sunucuya bağlanılıyor…",
  "start.title": "Eller serbest, herkesi tanıyan asistan",
  "start.lead":
    "Konuşmaya başlayın. Kendinizi ‘Ben Tuncer’ diye tanıtırsanız sizi tanırım; tanıtılmayan sesleri ‘3. kişi’ gibi etiketlerim.",
  "start.privacy":
    "Ses bu bilgisayardan çıkmaz. Ses izleri yalnızca sesli onayınızla, şifreli olarak kaydedilir; dil modeline sadece konuşmanın metni gider.",
  "start.opening": "Mikrofon açılıyor…",
  "start.retry": "Tekrar dene",
  "start.start": "Başlat",
  "start.withoutMic": "Mikrofonsuz devam et",
  "start.permGranted": "Mikrofon izni zaten verilmiş.",
  "start.permDenied": "Mikrofon izni engellenmiş.",
  "start.permPrompt": "Başlat'a basınca tarayıcı mikrofon izni ister; {assistant} ondan sonra dinler.",

  "capture.denied.title": "Mikrofon izni verilmedi",
  "capture.denied.body": "Adres çubuğundaki kilit simgesine tıklayıp mikrofona izin verin, ardından tekrar deneyin.",
  "capture.no_device.title": "Mikrofon bulunamadı",
  "capture.no_device.body": "Bir mikrofon bağlayın ya da sistem ayarlarından etkinleştirin, sonra tekrar deneyin.",
  "capture.busy.title": "Mikrofon kullanılamıyor",
  "capture.busy.body": "Mikrofon başka bir uygulama tarafından kullanılıyor olabilir. Onu kapatıp tekrar deneyin.",
  "capture.insecure.title": "Güvenli bağlantı gerekiyor",
  "capture.insecure.body":
    "Tarayıcı mikrofonu yalnızca https ya da 127.0.0.1 üzerinden açar. Sayfayı {url} adresinden açın.",
  "capture.unsupported.title": "Tarayıcı desteklemiyor",
  "capture.unsupported.body":
    "Bu tarayıcı gereken ses özelliklerini sunmuyor. Güncel bir Chrome, Edge ya da Firefox deneyin.",
  "capture.unknown.title": "Ses başlatılamadı",
  "capture.unknown.body": "Beklenmeyen bir sorun oluştu. Sayfayı yenileyip tekrar deneyin.",

  "notice.info": "Bilgi",
  "notice.warn": "Uyarı",
  "notice.error": "Hata",
  "toast.dismiss": "Kapat",
  "toast.micLost": "Mikrofon bağlantısı kesildi; varsayılan mikrofona geçiliyor.",

  "settings.title": "Ayarlar",
  "settings.close": "Ayarları kapat",
  "settings.mic": "Mikrofon",
  "settings.inputDevice": "Giriş aygıtı",
  "settings.micActive": "Etkin: {mic} · {rate} · yankı ve gürültü bastırma açık",
  "settings.micResampled": "{khz} kHz → 16 kHz (filtreli yeniden örnekleme)",
  "settings.micNative": "16 kHz",
  "settings.micListenOnly": "Mikrofonsuz moddasınız. Mikrofonu açmak için sayfayı yenileyip Başlat'a basın.",
  "settings.micHint": "Mikrofon adları, Başlat'a bastıktan ve izin verdikten sonra görünür.",
  "settings.language": "Dil",
  "settings.languageName": "Türkçe",
  "settings.languageServer": "sunucu belirledi",
  "settings.languageBrowser": "sunucuya bağlanana kadar tarayıcınızın dili",
  "settings.languageHint":
    "Değiştirmek için Utterlane Live'ı {cli} ile başlatın ya da {file} dosyasında {env} değerini ayarlayın.",
  "settings.appearance": "Görünüm",
  "settings.theme": "Tema",
  "settings.themeSystem": "Sistem",
  "settings.themeDark": "Koyu",
  "settings.themeLight": "Açık",
  "settings.showSilent": "{assistant} sessiz kaldığında göster",

  "profiles.title": "Kayıtlı ses profilleri",
  "profiles.hint":
    "Sesli onay verenlerin ses izleri. Bir profili silmek, o kişinin bir dahaki sefere yeniden tanıtılmasını gerektirir.",
  "profiles.error": "Profiller alınamadı. Sunucu çalışıyor mu?",
  "profiles.none": "Kayıtlı profil yok.",
  "profiles.unnamed": "İsimsiz",
  "profiles.samples": "{n} örnek",
  "profiles.delete": "Sil",
  "profiles.deleteOne": "{name} profilini sil",
  "profiles.deleteAll": "Tüm profilleri sil",
  "profiles.deleteAllAsk": "Tüm profiller silinsin mi?",
  "profiles.deleteAllConfirm": "{count} kalıcı olarak silinsin mi? Bu geri alınamaz.",
  "profiles.allCount": "{n} profilin tamamı",
  "profiles.deleteAllButton": "Hepsini sil",
  "profiles.deleted": "Ses profili silindi.",
  "profiles.deletedAll": "Tüm ses profilleri silindi.",
  "profiles.deleteFailed": "Silme başarısız oldu.",

  "budget.title": "Bütçe",
  "budget.unlimited": "sınırsız",
  "budget.hint": "Bu oturumda {n} dil modeli çağrısı. Sınıra ulaşıldığında {assistant} yanıt vermeyi durdurur.",

  "brain.title": "Yapay zekâ beyni",
  "brain.listenOnlyTitle": "Yalnızca dinleme modu",
  "brain.listenOnlyBody":
    "Yapay zekâ beyni yapılandırılmadığı için {assistant} konuşmayı yazıya döküp konuşmacıları etiketler ama yanıt vermez.",
  "brain.howTo":
    "Beyin eklemek için {file} dosyasına {openaiKey} ekleyin, {chat} ile Ollama üzerinden yerel bir model çalıştırın ya da OpenClaw veya Hermes Agent bağlayın. Her seçenek {docs} dosyasında anlatılıyor.",
  "brain.restart": "Değişiklikten sonra Utterlane Live'ı yeniden başlatın.",
  "brain.off": "Yok — yalnızca dinleme",
  "brainKind.openai": "OpenAI",
  "brainKind.openclaw": "OpenClaw",
  "brainKind.hermes": "Hermes Agent",
  "brainKind.ollama": "Ollama (yerel)",
  "brainKind.lmstudio": "LM Studio (yerel)",
  "brainKind.chat": "OpenAI uyumlu sunucu",
  "brainKind.responses": "OpenAI uyumlu sunucu (Responses API)",
  "brainKind.webhook": "Webhook",
  "brainKind.mock": "Deneme yanıtları (geliştirme)",
  "brainKind.none": "Yok",

  "about.title": "Hakkında",
  "about.serverLoading": "Sunucu modelleri hâlâ yüklüyor…",
  "about.error": "Sistem bilgisi alınamadı. Sunucu çalışıyor mu?",
  "about.waiting": "Sunucudan bilgi bekleniyor…",
  "about.version": "Sürüm",
  "about.hardware": "Donanım",
  "about.keyStorage": "Profil anahtarı",
  "about.latency": "Sunucu gecikmesi",
  "about.latencyValue": "{ms} ms",
  "about.github": "GitHub",
  "about.githubTitle": "GitHub'daki kaynak kodu (yeni sekmede açılır)",
  "model.stt": "Konuşmadan yazıya",
  "model.tts": "Ses",
  "model.llm": "Yapay zekâ beyni",
  "model.embedding": "Ses kimliği",
  "model.vad": "Ses algılama",
  "modelStatus.ready": "hazır",
  "modelStatus.loading": "yükleniyor",
  "modelStatus.error": "hata",
  "modelStatus.mock": "deneme",
  "modelStatus.off": "kapalı",
  "keyStorage.os_credential_store": "İşletim sistemi anahtar deposu",
  "keyStorage.file": "Yalnızca sahibinin okuyabildiği anahtar dosyası",

  "privacy.title": "Gizlilik",
  "privacy.local": "Ses izleri (konuşmacıyı tanımaya yarayan sayısal özetler) yalnızca bu bilgisayarda kalır.",
  "privacy.consent": "Bir ses izi ancak o kişi sesli olarak onay verdikten sonra kaydedilir.",
  "privacy.encryptedOs": "Kayıtlı ses izleri, işletim sisteminin anahtar deposunda tutulan bir anahtarla şifrelenir.",
  "privacy.encryptedFile":
    "Kayıtlı ses izleri, bu bilgisayarda yalnızca sahibinin okuyabildiği bir dosyada tutulan bir anahtarla şifrelenir (işletim sistemi anahtar deposu bulunamadı).",
  "privacy.textOnly": "Ses kayıtları dışarı gönderilmez; dil modeline yalnızca konuşmanın yazıya dökülmüş metni gider.",
  "privacy.label": "Ses eşleşmesi yalnızca bir etikettir; hiçbir yetki ya da kişisel bilgi açmaz.",

  "session.title": "Oturum",
  "session.reset": "Oturumu sıfırla",
  "session.resetAsk": "Oturum sıfırlansın mı?",
  "session.resetBody": "Konuşma geçmişi ve bu oturumdaki konuşmacılar silinir. Kayıtlı ses profilleri kalır.",
  "session.resetConfirm": "Sıfırla",
};

export const MESSAGES: Readonly<Record<Lang, Readonly<Record<MessageKey, Entry>>>> = { en, tr };

/** True for keys built at runtime (`status.${state}`) that exist in the dictionary. */
export function isMessageKey(key: string): key is MessageKey {
  return Object.hasOwn(en, key);
}

// ---------------------------------------------------------------------------
// Language selection

/** `navigator.language` → "tr" for any Turkish locale (tr, tr-TR, tr-CY …), otherwise "en". */
export function browserLanguage(tag: string | null | undefined): Lang {
  return typeof tag === "string" && /^tr(?:[-_]|$)/i.test(tag.trim()) ? "tr" : "en";
}

export interface LanguageSources {
  /** `ready.language` from the WebSocket. */
  ready?: Lang | null;
  /** `language` from `GET /api/settings`. */
  settings?: Lang | null;
  /** `browserLanguage(navigator.language)`. */
  browser: Lang;
}

/**
 * The server's conversation language is the source of truth once it is known
 * (`ready.language`, then `/api/settings`); until then the browser's language is used.
 */
export function resolveLanguage(sources: LanguageSources): { lang: Lang; fromServer: boolean } {
  const server = sources.ready ?? sources.settings ?? null;
  return server ? { lang: server, fromServer: true } : { lang: sources.browser, fromServer: false };
}

// ---------------------------------------------------------------------------
// Translation

export interface Translator {
  readonly lang: Lang;
  /** BCP 47 tag for Intl formatters (dates, plural rules). */
  readonly locale: string;
  t(key: MessageKey, vars?: Vars): string;
  /** Like `t`, but `{slot}` placeholders named in `slots` become React nodes. */
  rich(key: MessageKey, slots: Record<string, ReactNode>, vars?: Vars): ReactNode[];
}

const LOCALES: Record<Lang, string> = { en: "en-US", tr: "tr-TR" };

/** Placeholder names in a template, e.g. "{n} people" → ["n"]. */
export function placeholders(template: string): string[] {
  return [...template.matchAll(/\{(\w+)\}/g)].map((match) => match[1]);
}

export function interpolate(template: string, vars?: Vars): string {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) => (Object.hasOwn(vars, name) ? String(vars[name]) : match));
}

function withSlots(text: string, slots: Record<string, ReactNode>): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(/\{(\w+)\}/g)) {
    const name = match[1];
    const index = match.index ?? 0;
    if (!Object.hasOwn(slots, name)) continue;
    if (index > last) out.push(text.slice(last, index));
    out.push(createElement(Fragment, { key: `${name}:${index}` }, slots[name]));
    last = index + match[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function createTranslator(lang: Lang): Translator {
  const table = MESSAGES[lang] ?? MESSAGES[FALLBACK_LANG];
  const locale = LOCALES[lang] ?? LOCALES[FALLBACK_LANG];
  const rules = new Intl.PluralRules(locale);
  const template = (key: MessageKey, vars?: Vars): string => {
    const entry: Entry | undefined = table[key] ?? MESSAGES[FALLBACK_LANG][key];
    if (entry === undefined) return key;
    if (typeof entry === "string") return entry;
    const count = Number(vars?.n);
    return rules.select(Number.isFinite(count) ? count : 0) === "one" ? entry.one : entry.other;
  };
  return {
    lang,
    locale,
    t: (key, vars) => interpolate(template(key, vars), vars),
    rich: (key, slots, vars) => withSlots(interpolate(template(key, vars), vars), slots),
  };
}

const cache = new Map<Lang, Translator>();

/** One shared, stable translator per language (safe to use as a context value). */
export function translator(lang: Lang): Translator {
  let found = cache.get(lang);
  if (!found) {
    found = createTranslator(lang);
    cache.set(lang, found);
  }
  return found;
}

// ---------------------------------------------------------------------------
// React

export const I18nContext = createContext<Translator>(translator(FALLBACK_LANG));

/** `const { t } = useI18n();` inside any component rendered under App. */
export function useI18n(): Translator {
  return useContext(I18nContext);
}
