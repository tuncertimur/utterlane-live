#!/usr/bin/env node
// Utterlane Live mock server: a dependency-free stand-in for the Python backend (`utterlane`).
//
// - Serves the built UI from ../../src/utterlane_live/web, the folder `npm run build`
//   writes into and `pip install` ships (`npm run mock` builds first).
// - Implements the REST API and the /ws/live WebSocket (minimal RFC 6455, no packages).
// - Plays a scripted multi-speaker demo, in English or Turkish, with synthetic UTA1
//   assistant audio.
//
// Usage: node dev/mock-server.mjs [--port 5175] [--host 127.0.0.1] [--speed 1]
//                                  [--language en|tr] [--brain openai|chat|webhook|mock|none]
//                                  [--autostart] [--audio tone|silence] [--static <dir>]
// --language: the conversation language sent in `ready` and /api/settings (default:
//   UTTERLANE_LANGUAGE, else "tr" like the backend). --brain none: listen-only mode, the
//   assistant never generates replies (`assistant_silent` reason "no_brain").
// The demo starts on the first microphone frame (after "Start"), 1.5 s after connect
// with --autostart, or after 10 s without audio. "Reset session" in the UI replays it.

import { createHash, randomBytes } from "node:crypto";
import { readFile, stat } from "node:fs/promises";
import { createServer } from "node:http";
import { arch, availableParallelism, type as osType } from "node:os";
import { extname, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

// ----------------------------------------------------------------- options

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (!arg.startsWith("--")) continue;
    const key = arg.slice(2);
    const next = argv[i + 1];
    if (next === undefined || next.startsWith("--")) out[key] = true;
    else {
      out[key] = next;
      i += 1;
    }
  }
  return out;
}

function choice(name, value, allowed, fallback) {
  if (value === undefined || value === "") return fallback;
  if (allowed.includes(value)) return value;
  console.error(`[mock] --${name} must be one of: ${allowed.join(", ")}`);
  process.exit(2);
}

const args = parseArgs(process.argv.slice(2));
const PORT = Number(args.port ?? process.env.PORT ?? 5175);
const HOST = String(args.host ?? "127.0.0.1");
const SPEED = Math.max(0.25, Math.min(10, Number(args.speed ?? process.env.MOCK_SPEED ?? 1) || 1));
const AUTOSTART = Boolean(args.autostart) || process.env.MOCK_AUTOSTART === "1";
const AUDIO = String(args.audio ?? process.env.MOCK_AUDIO ?? "tone");
const LANG = choice("language", args.language ?? process.env.UTTERLANE_LANGUAGE, ["en", "tr"], "tr");
const BRAIN = choice("brain", args.brain ?? process.env.UTTERLANE_BRAIN, ["openai", "chat", "webhook", "mock", "none"], "mock");
const BRAIN_OFF = BRAIN === "none";
const DIST = args.static
  ? resolve(String(args.static))
  : resolve(fileURLToPath(new URL("../../src/utterlane_live/web/", import.meta.url)));
const TTS_RATE = 24000;
const MAX_PAYLOAD = 1 << 20;

// ------------------------------------------------------------- static files

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".json": "application/json; charset=utf-8",
  ".woff2": "font/woff2",
  ".map": "application/json; charset=utf-8",
};

async function serveStatic(pathname, res) {
  let relative = decodeURIComponent(pathname);
  if (relative.endsWith("/")) relative += "index.html";
  let file = resolve(DIST, `.${relative}`);
  if (file !== DIST && !file.startsWith(DIST + sep)) {
    res.writeHead(403).end();
    return;
  }
  try {
    const info = await stat(file);
    if (info.isDirectory()) file = resolve(file, "index.html");
  } catch {
    file = resolve(DIST, "index.html"); // SPA fallback
  }
  try {
    const body = await readFile(file);
    const type = MIME[extname(file)] ?? "application/octet-stream";
    const cache = file.includes(`${sep}assets${sep}`) ? "public, max-age=31536000, immutable" : "no-store";
    res.writeHead(200, { "Content-Type": type, "Cache-Control": cache, "X-Content-Type-Options": "nosniff" });
    res.end(body);
  } catch {
    res.writeHead(503, { "Content-Type": "text/html; charset=utf-8" });
    res.end(
      "<!doctype html><meta charset=utf-8><title>Utterlane Live</title><body style='font-family:system-ui;background:#111;color:#eee;padding:2rem'>" +
        "<h1>The web UI is not built</h1><p>Run <code>npm run build</code> in <code>web/</code>, or <code>npm run mock</code>, which builds first.</p></body>",
    );
  }
}

// ------------------------------------------------- minimal WebSocket (RFC 6455)

const WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";

class MiniSocket {
  constructor(socket, head) {
    this.socket = socket;
    this.buffer = head && head.length ? Buffer.from(head) : Buffer.alloc(0);
    this.fragments = [];
    this.fragmentOpcode = 0;
    this.open = true;
    this.onText = () => {};
    this.onBinary = () => {};
    this.onClose = () => {};
    socket.setNoDelay(true);
    socket.on("data", (chunk) => {
      this.buffer = this.buffer.length ? Buffer.concat([this.buffer, chunk]) : chunk;
      this.parse();
    });
    socket.on("close", () => this.finish());
    socket.on("error", () => this.finish());
    if (this.buffer.length) queueMicrotask(() => this.parse());
  }

  parse() {
    while (this.open && this.buffer.length >= 2) {
      const b0 = this.buffer[0];
      const b1 = this.buffer[1];
      const fin = (b0 & 0x80) !== 0;
      const opcode = b0 & 0x0f;
      const masked = (b1 & 0x80) !== 0;
      let length = b1 & 0x7f;
      let offset = 2;
      if (length === 126) {
        if (this.buffer.length < 4) return;
        length = this.buffer.readUInt16BE(2);
        offset = 4;
      } else if (length === 127) {
        if (this.buffer.length < 10) return;
        const big = this.buffer.readBigUInt64BE(2);
        if (big > BigInt(MAX_PAYLOAD)) return this.close(1009);
        length = Number(big);
        offset = 10;
      }
      if (!masked) return this.close(1002); // clients must mask
      if (length > MAX_PAYLOAD) return this.close(1009);
      if (this.buffer.length < offset + 4 + length) return;
      const mask = this.buffer.subarray(offset, offset + 4);
      const payload = Buffer.allocUnsafe(length);
      const start = offset + 4;
      for (let i = 0; i < length; i += 1) payload[i] = this.buffer[start + i] ^ mask[i & 3];
      this.buffer = this.buffer.subarray(start + length);
      this.frame(fin, opcode, payload);
    }
  }

  frame(fin, opcode, payload) {
    if (opcode === 0x8) {
      const code = payload.length >= 2 ? payload.readUInt16BE(0) : 1000;
      return this.close(code === 1005 ? 1000 : code);
    }
    if (opcode === 0x9) return this.write(0xa, payload); // ping → pong
    if (opcode === 0xa) return; // pong
    if (opcode === 0x1 || opcode === 0x2) {
      this.fragmentOpcode = opcode;
      this.fragments = [];
    } else if (opcode !== 0x0) {
      return this.close(1002);
    }
    this.fragments.push(payload);
    if (!fin) return;
    const data = this.fragments.length === 1 ? this.fragments[0] : Buffer.concat(this.fragments);
    this.fragments = [];
    if (this.fragmentOpcode === 0x1) this.onText(data.toString("utf8"));
    else this.onBinary(data);
  }

  write(opcode, payload) {
    if (!this.open) return;
    const length = payload.length;
    let header;
    if (length < 126) {
      header = Buffer.from([0x80 | opcode, length]);
    } else if (length < 65536) {
      header = Buffer.alloc(4);
      header[0] = 0x80 | opcode;
      header[1] = 126;
      header.writeUInt16BE(length, 2);
    } else {
      header = Buffer.alloc(10);
      header[0] = 0x80 | opcode;
      header[1] = 127;
      header.writeBigUInt64BE(BigInt(length), 2);
    }
    this.socket.write(Buffer.concat([header, payload]));
  }

  sendJson(message) {
    this.write(0x1, Buffer.from(JSON.stringify(message), "utf8"));
  }

  sendBinary(buffer) {
    this.write(0x2, buffer);
  }

  close(code = 1000) {
    if (!this.open) return;
    const payload = Buffer.alloc(2);
    payload.writeUInt16BE(code, 0);
    this.write(0x8, payload);
    this.finish();
    this.socket.end();
  }

  finish() {
    if (!this.open) return;
    this.open = false;
    this.onClose();
  }
}

// -------------------------------------------------------------- demo session

const sleep = (ms) => new Promise((done) => setTimeout(done, ms));
const now = () => Date.now() / 1000;

class Aborted extends Error {}

// Server-side text in both languages. Labels, identity events and notices use the same
// wording as the real backend (src/utterlane_live/i18n.py); the conversation is made up.
const TEXT = {
  en: {
    person: (n) => `Person ${n}`,
    unknown: "Unclear",
    loading: "Preparing the voice models…",
    replaced: "Another tab connected.",
    mode: { smart: "smart", always: "answer everything", wake: "only when addressed" },
    modeChanged: (mode) => `Mode: ${mode}.`,
    sessionReset: "Session reset. Saved voices are kept.",
    newSpeaker: (label) => `New voice heard: ${label}.`,
    named: (previous, name) => `${previous} introduced themselves as ${name}.`,
    manualNamed: (name) => `Named ${name} manually.`,
    manualCleared: (label) => `Name removed; now ${label}.`,
    forgotten: (label) => `${label} was forgotten; the voice print was deleted.`,
    profileSaved: (name) => `${name} agreed; their voice print is stored encrypted on this computer.`,
    provisional: (name) => `The floor was handed to ${name}; this voice is provisionally ${name}.`,
    confirmed: (name) => `${name} kept talking with no objection; the name is now confirmed.`,
    demoStart: "Demo mode: the mock server is playing a sample conversation.",
    demoDone: "Demo finished. Settings › Reset session plays it again.",
    // The demo conversation.
    a: "Alex",
    b: "Sam",
    d: "Jordan",
    t1: "Hi, I'm Alex. Today we're trying out the new voice assistant.",
    r1: ["Hi Alex, I'm Lena. Nice to meet you.", "Would you like me to recognize your voice next time too?"],
    askConsent: "Alex, would you like me to recognize your voice next time too?",
    t2: "Yes, please do.",
    r2: ["Done, I've saved it.", "You can delete it in Settings any time."],
    t3: "Hi Lena, I'm Sam.",
    r3: ["Hi Sam, welcome!", "There are two of you now, so I'll speak when you call me."],
    t4: "What time does the meeting start again?",
    t5: "At three, but we might run a bit late.",
    t6: "Yeah, sure, fine.",
    t7: "Okay.",
    t8: "Lena, Jordan is here too. Jordan, go ahead.",
    r8: ["Sure, I'm listening, Jordan."],
    t9: "Hi, yes, that's me. Lena, could you suggest a short agenda?",
    r9: ["Of course: first last week's summary, then the live demo, and questions at the end.", "Ten minutes each?"],
    t10: "Sounds good. By the way, I'm Jordan, nice to meet you.",
    t11: "Lena, could you explain this demo a little?",
    r11: [
      "Sure. Utterlane Live is a hands-free assistant that tells everyone in the room apart by their voice.",
      "To know who is talking, it keeps a short voice print only on this computer.",
      "Only the text of the conversation goes to the language model, so audio recordings never leave the machine.",
    ],
    t12: "Lena, hold on a second.",
    r12: ["Sure, I've stopped. I'm listening, Alex."],
  },
  tr: {
    person: (n) => `${n}. kişi`,
    unknown: "Belirsiz",
    loading: "Ses modelleri hazırlanıyor…",
    replaced: "Başka bir sekme bağlandı.",
    mode: { smart: "akıllı", always: "her şeye cevap", wake: "sadece hitap edilince" },
    modeChanged: (mode) => `Mod: ${mode}.`,
    sessionReset: "Oturum sıfırlandı. Kayıtlı sesler korunuyor.",
    newSpeaker: (label) => `Yeni bir ses duyuldu: ${label}.`,
    named: (previous, name) => `${previous} kendini ${name} olarak tanıttı.`,
    manualNamed: (name) => `${name} adı elle verildi.`,
    manualCleared: (label) => `İsim kaldırıldı; artık ${label}.`,
    forgotten: (label) => `${label} unutuldu; ses izi silindi.`,
    profileSaved: (name) => `${name} onay verdi; ses izi bu bilgisayarda şifreli olarak saklandı.`,
    provisional: (name) => `Söz ${name}'e verildi; bu ses geçici olarak ${name} sayıldı.`,
    confirmed: (name) => `${name} itiraz olmadan konuşmaya devam etti; isim kesinleşti.`,
    demoStart: "Demo modu: sahte sunucu örnek bir konuşmayı oynatıyor.",
    demoDone: "Demo tamamlandı. Ayarlar › Oturumu sıfırla ile yeniden oynatabilirsiniz.",
    a: "Tuncer",
    b: "Zümra",
    d: "Ahmet",
    t1: "Merhaba, ben Tuncer. Bugün yeni sesli asistanı deneyeceğiz.",
    r1: ["Merhaba Tuncer, ben Lena; tanıştığımıza memnun oldum.", "Sesini bu bilgisayarda şifreli olarak saklamamı ister misin?"],
    askConsent: "Tuncer, bir dahaki sefere de seni sesinden tanımamı ister misin?",
    t2: "Evet, saklayabilirsin.",
    r2: ["Tamam, kaydettim.", "İstediğin zaman ayarlardan silebilirsin."],
    t3: "Merhaba Lena, ben de Zümra.",
    r3: ["Merhaba Zümra, hoş geldin!", "Artık iki kişisiniz; bana seslendiğinizde konuşacağım."],
    t4: "Toplantı saat kaçta başlıyordu?",
    t5: "Üçte, ama biraz gecikebiliriz.",
    t6: "Evet evet, olur.",
    t7: "Tamam.",
    t8: "Lena, Ahmet de burada. Ahmet, sen konuş.",
    r8: ["Tabii, dinliyorum Ahmet."],
    t9: "Selam, evet benim. Lena, bize kısa bir gündem önerir misin?",
    r9: ["Elbette: önce geçen haftanın özeti, sonra canlı demo, en son da sorular.", "Her biri on dakika olsun mu?"],
    t10: "Olur. Bu arada ben Ahmet, memnun oldum.",
    t11: "Lena, bu demoyu biraz anlatır mısın?",
    r11: [
      "Elbette. Utterlane Live, odadaki herkesi sesinden ayırt eden, eller serbest çalışan bir asistan.",
      "Kimin konuştuğunu anlamak için sesin kısa bir izini yalnızca bu bilgisayarda tutuyor.",
      "Dil modeline ise sadece konuşmanın metni gidiyor; böylece ses kayıtları hiçbir zaman dışarı çıkmıyor.",
    ],
    t12: "Lena, bir saniye dur.",
    r12: ["Tabii, durdum. Seni dinliyorum Tuncer."],
  },
};
const S = TEXT[LANG];

// What the real backend reports for each brain (`models.llm.id`); the mock never calls one.
const BRAIN_IDS = {
  openai: "openai gpt-6-luna",
  chat: "chat qwen3:8b @ http://127.0.0.1:11434/v1",
  webhook: "webhook http://127.0.0.1:9000/turn",
  mock: "mock (local placeholder replies)",
  none: "none (listen only)",
};

const UNKNOWN = {
  id: "unknown",
  label: S.unknown,
  name: null,
  name_state: "none",
  ordinal: 0,
  persistent: false,
  present: true,
  turns: 0,
  color: -1,
};

function labelOf(speaker) {
  if (speaker.name && speaker.name_state === "confirmed") return speaker.name;
  if (speaker.name && speaker.name_state === "provisional") return `${speaker.name}?`;
  return S.person(speaker.ordinal);
}

function synthSpeech(seconds, seed) {
  // A soft, voice-like hum: three harmonics with a syllable-rate envelope (≈ -26 dBFS).
  const count = Math.max(1, Math.round(seconds * TTS_RATE));
  const pcm = new Int16Array(count);
  if (AUDIO === "silence") return pcm;
  const f0 = 170 + (seed % 4) * 14;
  for (let i = 0; i < count; i += 1) {
    const t = i / TTS_RATE;
    const syllable = 0.55 - 0.45 * Math.cos(2 * Math.PI * (3.6 + (seed % 3) * 0.5) * t);
    const fade = Math.min(1, t / 0.03, (seconds - t) / 0.06);
    const wave =
      Math.sin(2 * Math.PI * f0 * t) * 0.62 + Math.sin(2 * Math.PI * 2 * f0 * t) * 0.26 + Math.sin(2 * Math.PI * 3 * f0 * t) * 0.12;
    pcm[i] = Math.round(wave * syllable * Math.max(0, fade) * 0.05 * 32767);
  }
  return pcm;
}

function uta1(seq, index, rate, pcm) {
  const out = Buffer.alloc(16 + pcm.length * 2);
  out.write("UTA1", 0, "ascii");
  out.writeUInt32LE(seq, 4);
  out.writeUInt32LE(index, 8);
  out.writeUInt32LE(rate, 12);
  for (let i = 0; i < pcm.length; i += 1) out.writeInt16LE(pcm[i], 16 + i * 2);
  return out;
}

class MockSession {
  constructor() {
    this.transport = null;
    this.profiles = [];
    this.ledger = { spent_usd: 0, cap_usd: 5, calls: 0 };
    this.mode = "smart";
    this.listening = true;
    this.firstAttach = true;
    this.gen = 0;
    this.scriptState = "idle"; // idle | waiting | running | done
    this.fallbackTimer = null;
    this.lastLevelAt = 0;
    this.simulating = false;
    this.clear();
  }

  clear() {
    this.sessionId = `s-${randomBytes(4).toString("hex")}`;
    this.speakers = new Map();
    this.turns = [];
    this.replies = [];
    this.turnSeq = 0;
    this.replySeq = this.replySeq ?? 0;
    this.current = null;
    this.audible = null;
    this.audioUntil = 0;
    this.status = "listening";
  }

  // --- transport -----------------------------------------------------------

  send(message) {
    this.transport?.sendJson(message);
  }

  sendAudio(buffer) {
    this.transport?.sendBinary(buffer);
  }

  attach(ws) {
    const previous = this.transport;
    if (previous && previous.open) {
      previous.sendJson({ type: "notice", level: "warn", code: "replaced", message: S.replaced });
    }
    this.transport = ws;
    ws.onText = (text) => {
      let message;
      try {
        message = JSON.parse(text);
      } catch {
        return;
      }
      if (message && typeof message === "object") this.onControl(message, ws);
    };
    ws.onBinary = (data) => this.onAudio(data, ws);
    ws.onClose = () => {
      if (this.transport === ws) this.transport = null;
    };

    this.send({
      type: "ready",
      session_id: this.sessionId,
      assistant_name: "Lena",
      language: LANG,
      mode: this.mode,
      listening: this.listening,
      models: this.models(),
      budget: { spent_usd: this.ledger.spent_usd, cap_usd: this.ledger.cap_usd },
    });
    this.sendRoster();
    if (this.turns.length) {
      this.send({
        type: "history",
        turns: this.turns.map((turn) => this.publicTurn(turn)),
        // Proposed protocol extension: lets a reloaded page show Lena's replies too.
        replies: this.replies.map(({ id, in_reply_to, text, interrupted }) => ({ reply_id: id, in_reply_to, text, interrupted })),
      });
    }
    if (this.firstAttach) {
      this.firstAttach = false;
      this.setStatus("loading", S.loading, true);
      setTimeout(() => this.setStatus(this.listening ? "listening" : "paused", null, true), 1500 / SPEED);
    } else {
      this.setStatus(this.listening ? "listening" : "paused", null, true);
    }
    this.send({ type: "usage", ...this.ledger });

    if (this.scriptState === "idle") {
      this.scriptState = "waiting";
      const delay = AUTOSTART ? 1500 : 10_000;
      this.fallbackTimer = setTimeout(() => this.startScript("timer"), delay);
    }
  }

  models() {
    return {
      vad: { id: "mock-vad", status: "mock" },
      stt: { id: `mock-stt-${LANG} (cpu)`, status: "mock" },
      embedding: { id: "mock-voiceprint", status: "mock" },
      llm: BRAIN_OFF
        ? { id: BRAIN_IDS.none, status: "off" }
        : { id: BRAIN === "mock" ? BRAIN_IDS.mock : `${BRAIN_IDS[BRAIN]} (mock)`, status: "mock" },
      // The real backend puts the voice's license attribution here (CC-BY); see voices.py.
      tts: {
        id: "mock-tone-24k",
        status: "mock",
        detail: "Mock voice: a synthetic tone. The real voice shows its license attribution on this line.",
      },
    };
  }

  health() {
    return {
      status: "ok",
      version: "0.1.0-mock",
      language: LANG,
      models: this.models(),
      hardware: `${osType()} ${arch()}, ${availableParallelism()} CPU threads, no CUDA GPU (mock server)`,
      gpu: { name: null, available: false },
      profiles_key_storage: "os_credential_store",
    };
  }

  settings() {
    return {
      mode: this.mode,
      assistant_name: "Lena",
      language: LANG,
      budget_cap_usd: this.ledger.cap_usd,
      tts_voice: "mock",
      brain: BRAIN,
    };
  }

  setStatus(state, detail = null, force = false) {
    if (!force && this.status === state && !detail) return;
    this.status = state;
    this.send(detail ? { type: "status", state, detail } : { type: "status", state });
  }

  notice(level, code, message) {
    this.send({ type: "notice", level, code, message });
  }

  sendRoster() {
    this.send({ type: "speakers", speakers: [...this.speakers.values()].map((s) => this.publicSpeaker(s)) });
  }

  publicSpeaker(speaker) {
    if (!speaker || speaker.id === "unknown") return { ...UNKNOWN };
    return {
      id: speaker.id,
      label: labelOf(speaker),
      name: speaker.name,
      name_state: speaker.name_state,
      ordinal: speaker.ordinal,
      persistent: speaker.persistent,
      present: speaker.present,
      turns: speaker.turns,
      color: speaker.color,
    };
  }

  publicTurn(turn) {
    const { speaker_id, ...rest } = turn;
    return { ...rest, speaker: this.publicSpeaker(this.speakers.get(speaker_id)) };
  }

  identity(kind, speakerId, message, name = null) {
    this.send({ type: "identity_event", kind, speaker_id: speakerId, name, message });
  }

  relabelTurnsOf(speakerId) {
    for (const turn of this.turns) {
      if (turn.speaker_id === speakerId) {
        this.send({ type: "turn_update", turn_id: turn.id, speaker: this.publicSpeaker(this.speakers.get(speakerId)) });
      }
    }
  }

  // --- client input --------------------------------------------------------

  onControl(message, ws) {
    if (ws !== this.transport) return;
    switch (message.type) {
      case "hello":
        console.log(`[mock] hello from ${message.client ?? "?"} v${message.version ?? "?"}`);
        break;
      case "ping":
        this.send({ type: "pong", t: message.t ?? null });
        break;
      case "playback":
        if (message.state === "started") {
          this.audible = message.reply_id;
          this.setStatus("speaking");
        } else if (message.state === "ended" || message.state === "stopped") {
          if (this.audible === message.reply_id || !message.reply_id) this.audible = null;
          if (!this.current && this.status === "speaking") this.setStatus(this.listening ? "listening" : "paused");
        }
        break;
      case "set_mode":
        if (["smart", "always", "wake"].includes(message.mode)) {
          this.mode = message.mode;
          this.notice("info", "mode_changed", S.modeChanged(S.mode[this.mode]));
        }
        break;
      case "listen":
        this.listening = message.enabled !== false;
        this.setStatus(this.listening ? "listening" : "paused");
        break;
      case "rename_speaker":
        this.rename(String(message.speaker_id ?? ""), String(message.name ?? "").trim().slice(0, 40));
        break;
      case "forget_speaker":
        this.forget(String(message.speaker_id ?? ""));
        break;
      case "reset_session":
        this.reset();
        break;
      default:
        break;
    }
  }

  onAudio(data, ws) {
    if (ws !== this.transport || !this.listening || data.length % 2) return;
    if (this.scriptState === "waiting") this.startScript("microphone");
    const t = Date.now();
    if (this.simulating || t - this.lastLevelAt < 100) return;
    this.lastLevelAt = t;
    let sum = 0;
    const count = data.length / 2;
    for (let i = 0; i < count; i += 1) {
      const sample = data.readInt16LE(i * 2) / 32768;
      sum += sample * sample;
    }
    const rms = Math.sqrt(sum / Math.max(1, count));
    this.send({ type: "level", rms: Math.min(1, Math.round(rms * 4 * 1000) / 1000), speech: rms > 0.03 });
  }

  rename(speakerId, name) {
    const speaker = this.speakers.get(speakerId);
    if (!speaker) return;
    if (name) {
      speaker.name = name;
      speaker.name_state = "confirmed";
      this.identity("corrected", speakerId, S.manualNamed(name), name);
    } else {
      speaker.name = null;
      speaker.name_state = "none";
      this.identity("corrected", speakerId, S.manualCleared(labelOf(speaker)));
    }
    this.relabelTurnsOf(speakerId);
    this.sendRoster();
  }

  forget(speakerId) {
    const speaker = this.speakers.get(speakerId);
    if (!speaker) return;
    this.speakers.delete(speakerId);
    if (speaker.profileId) this.profiles = this.profiles.filter((profile) => profile.id !== speaker.profileId);
    for (const turn of this.turns) {
      if (turn.speaker_id === speakerId) {
        turn.speaker_id = "unknown";
        this.send({ type: "turn_update", turn_id: turn.id, speaker: { ...UNKNOWN } });
      }
    }
    this.identity("forgotten", speakerId, S.forgotten(labelOf(speaker)), speaker.name);
    this.sendRoster();
  }

  reset() {
    this.gen += 1;
    clearTimeout(this.fallbackTimer);
    if (this.audible) this.send({ type: "stop_playback", reply_id: this.audible, reason: "reset" });
    this.clear();
    this.send({ type: "history", turns: [] });
    this.sendRoster();
    this.notice("info", "session_reset", S.sessionReset);
    this.setStatus(this.listening ? "listening" : "paused", null, true);
    this.scriptState = "waiting";
    console.log("[mock] session reset; demo restarts in 3 s");
    this.fallbackTimer = setTimeout(() => this.startScript("reset"), 3000);
  }

  // --- script helpers ------------------------------------------------------

  async wait(ms, gen) {
    await sleep(ms / SPEED);
    if (gen !== this.gen) throw new Aborted();
  }

  addSpeaker(ordinal, extra = {}) {
    const speaker = {
      id: `spk-${ordinal}`,
      ordinal,
      color: (ordinal - 1) % 8,
      name: null,
      name_state: "none",
      persistent: false,
      present: true,
      turns: 0,
      profileId: null,
      ...extra,
    };
    this.speakers.set(speaker.id, speaker);
    return speaker;
  }

  async humanTurn(gen, speakerId, text, { attribution = "known", flags = [], hearMs = 1500, score = 0.78 } = {}) {
    if (!this.listening) await this.waitUntilListening(gen);
    this.simulating = true;
    this.setStatus("hearing");
    const started = now();
    const steps = Math.max(2, Math.round(hearMs / 100));
    for (let i = 0; i < steps; i += 1) {
      this.send({ type: "level", rms: Math.round((0.25 + Math.random() * 0.4) * 1000) / 1000, speech: true });
      await this.wait(100, gen);
    }
    this.send({ type: "level", rms: 0.02, speech: false });
    this.simulating = false;
    await this.wait(320, gen);
    const speaker = this.speakers.get(speakerId);
    if (speaker) speaker.turns += 1;
    const turn = {
      id: `t-${String(++this.turnSeq).padStart(6, "0")}`,
      speaker_id: speaker ? speakerId : "unknown",
      text,
      started_at: Math.round(started * 100) / 100,
      ended_at: Math.round(now() * 100) / 100,
      duration_s: Math.round((hearMs / 1000) * 100) / 100,
      stt_ms: 240 + Math.round(Math.random() * 180),
      attribution:
        attribution === "ambiguous" || attribution === "short"
          ? { state: attribution, score: attribution === "short" ? 0.41 : 0.38, margin: 0.03 }
          : { state: attribution, score, margin: Math.round((0.18 + Math.random() * 0.2) * 100) / 100 },
      flags,
    };
    this.turns.push(turn);
    this.send({ type: "turn", turn: this.publicTurn(turn) });
    this.sendRoster();
    return turn;
  }

  async waitUntilListening(gen) {
    while (!this.listening) await this.wait(250, gen);
  }

  silent(turn) {
    this.send({ type: "assistant_silent", in_reply_to: turn.id, reason: this.mode === "wake" ? "wake_word_missing" : "not_addressed" });
    this.setStatus("listening");
  }

  /**
   * Speak a reply. `fixed`: a canned sentence the backend says without a language model
   * (consent questions and confirmations), so it plays even in listen-only mode and costs
   * nothing. Anything else is a model reply: with `--brain none` it becomes a `no_brain`
   * silence, exactly like the backend.
   */
  async reply(gen, turn, sentences, { bargeIn = null, fixed = false } = {}) {
    if (BRAIN_OFF && !fixed) {
      this.send({ type: "assistant_silent", in_reply_to: turn?.id ?? null, reason: "no_brain" });
      this.setStatus(this.listening ? "listening" : "paused");
      await this.wait(500, gen);
      return null;
    }
    const reply = { id: `r-${randomBytes(4).toString("hex")}`, seq: ++this.replySeq, in_reply_to: turn?.id ?? null, text: "", interrupted: false };
    this.current = reply;
    this.setStatus("thinking");
    await this.wait(650, gen);
    this.send({ type: "assistant_start", reply_id: reply.id, reply_seq: reply.seq, in_reply_to: reply.in_reply_to });
    let chunks = 0;
    for (let index = 0; index < sentences.length; index += 1) {
      const sentence = sentences[index];
      const words = (index > 0 ? ` ${sentence}` : sentence).match(/\s*\S+/g) ?? [];
      for (const word of words) {
        reply.text += word;
        this.send({ type: "assistant_delta", reply_id: reply.id, text: word });
        await this.wait(55, gen);
      }
      // One audio chunk per sentence, like the real TTS pipeline.
      const seconds = Math.max(0.9, sentence.length * 0.058) / SPEED;
      const startAt = Math.max(Date.now(), this.audioUntil);
      this.audioUntil = startAt + seconds * 1000;
      this.sendAudio(uta1(reply.seq, chunks, TTS_RATE, synthSpeech(seconds, reply.seq + index)));
      chunks += 1;
      if (bargeIn && index + 1 === bargeIn.afterSentences) {
        await this.wait(bargeIn.delayMs, gen);
        // The barge-in detector fires while Lena is still talking.
        this.send({ type: "stop_playback", reply_id: reply.id, reason: "barge_in" });
        this.audible = null;
        this.audioUntil = 0;
        reply.interrupted = true;
        break;
      }
    }
    this.send({ type: "audio_end", reply_id: reply.id, reply_seq: reply.seq, chunks });
    let usage = null;
    if (!fixed) {
      const inputTokens = 380 + Math.round(Math.random() * 300);
      const outputTokens = Math.max(8, Math.round(reply.text.length / 3.6));
      const cost = Math.round((inputTokens * 0.4e-6 + outputTokens * 1.6e-6) * 1e6) / 1e6;
      this.ledger.spent_usd = Math.round((this.ledger.spent_usd + cost) * 1e6) / 1e6;
      this.ledger.calls += 1;
      usage = { input_tokens: inputTokens, output_tokens: outputTokens, cost_usd: cost };
    }
    this.send({
      type: "assistant_end",
      reply_id: reply.id,
      text: reply.text.trim(),
      interrupted: reply.interrupted,
      usage,
    });
    if (usage) this.send({ type: "usage", ...this.ledger });
    this.replies.push(reply);
    if (!reply.interrupted) {
      const remaining = this.audioUntil - Date.now();
      if (remaining > 0) await sleep(remaining);
      if (gen !== this.gen) throw new Aborted();
      await this.wait(350, gen);
    }
    this.current = null;
    if (!this.audible) this.setStatus(this.listening ? "listening" : "paused");
    return reply;
  }

  // --- the scripted demo ---------------------------------------------------

  startScript(trigger) {
    if (this.scriptState !== "waiting") return;
    clearTimeout(this.fallbackTimer);
    this.scriptState = "running";
    const gen = this.gen;
    console.log(`[mock] demo started (${trigger}), speed ×${SPEED}`);
    this.runScript(gen).then(
      () => {
        if (gen === this.gen) {
          this.scriptState = "done";
          console.log("[mock] demo finished");
        }
      },
      (error) => {
        if (!(error instanceof Aborted)) console.error("[mock] script error", error);
      },
    );
  }

  async runScript(gen) {
    await this.wait(900, gen);
    this.notice("info", "mock_demo", S.demoStart);
    await this.wait(1200, gen);

    // 1. A new voice introduces itself (Alex / Tuncer).
    this.addSpeaker(1);
    this.identity("new_speaker", "spk-1", S.newSpeaker(S.person(1)));
    const t1 = await this.humanTurn(gen, "spk-1", S.t1, { attribution: "new", flags: ["self_intro"], hearMs: 2400 });
    await this.wait(1100, gen);
    const first = this.speakers.get("spk-1");
    first.name = S.a;
    first.name_state = "confirmed";
    this.identity("named", "spk-1", S.named(S.person(1), S.a), S.a);
    this.relabelTurnsOf("spk-1");
    this.sendRoster();

    // 2. Lena greets them and asks for consent to keep the voice print. Without a brain
    //    the greeting is skipped (no_brain) but the canned consent question still plays.
    if (BRAIN_OFF) {
      await this.reply(gen, t1, S.r1);
      await this.reply(gen, t1, [S.askConsent], { fixed: true });
    } else {
      await this.reply(gen, t1, S.r1);
    }

    // 3. Spoken consent → profile saved.
    const t2 = await this.humanTurn(gen, "spk-1", S.t2, { flags: ["consent_yes"], score: 0.84, hearMs: 1200 });
    await this.wait(500, gen);
    first.persistent = true;
    first.profileId = "prof-1";
    this.profiles.push({ id: "prof-1", name: S.a, created_at: now(), updated_at: now(), samples: 3 });
    this.identity("profile_saved", "spk-1", S.profileSaved(S.a), S.a);
    this.sendRoster();
    await this.reply(gen, t2, S.r2, { fixed: true });

    // 4. A second person introduces themselves (Sam / Zümra).
    this.addSpeaker(2);
    this.identity("new_speaker", "spk-2", S.newSpeaker(S.person(2)));
    const t3 = await this.humanTurn(gen, "spk-2", S.t3, {
      attribution: "new",
      flags: ["self_intro", "addressed_assistant"],
      hearMs: 1600,
    });
    await this.wait(900, gen);
    const second = this.speakers.get("spk-2");
    second.name = S.b;
    second.name_state = "confirmed";
    this.identity("named", "spk-2", S.named(S.person(2), S.b), S.b);
    this.relabelTurnsOf("spk-2");
    this.sendRoster();
    await this.reply(gen, t3, S.r3);

    // 5. An unknown voice, side talk, crosstalk and a very short turn: Lena stays quiet.
    this.addSpeaker(3);
    this.identity("new_speaker", "spk-3", S.newSpeaker(S.person(3)));
    const t4 = await this.humanTurn(gen, "spk-3", S.t4, { attribution: "new", hearMs: 1500 });
    this.silent(t4);
    await this.wait(700, gen);
    const t5 = await this.humanTurn(gen, "spk-2", S.t5, { score: 0.79, hearMs: 1500 });
    this.silent(t5);
    await this.wait(600, gen);
    const t6 = await this.humanTurn(gen, "unknown", S.t6, { attribution: "ambiguous", hearMs: 900 });
    this.silent(t6);
    await this.wait(600, gen);
    const t7 = await this.humanTurn(gen, "spk-1", S.t7, { attribution: "short", hearMs: 500 });
    this.silent(t7);
    await this.wait(900, gen);

    // 6. The first speaker hands the floor over: the next new voice is provisionally "Jordan?".
    const t8 = await this.humanTurn(gen, "spk-1", S.t8, { flags: ["nomination", "addressed_assistant"], hearMs: 2000 });
    await this.reply(gen, t8, S.r8);
    this.addSpeaker(4, { name: S.d, name_state: "provisional" });
    this.identity("provisional", "spk-4", S.provisional(S.d), S.d);
    this.sendRoster();
    const t9 = await this.humanTurn(gen, "spk-4", S.t9, { attribution: "new", flags: ["addressed_assistant"], hearMs: 2300 });
    await this.reply(gen, t9, S.r9);
    await this.humanTurn(gen, "spk-4", S.t10, { flags: ["self_intro"], score: 0.74, hearMs: 1900 });
    await this.wait(700, gen);
    const fourth = this.speakers.get("spk-4");
    fourth.name_state = "confirmed";
    this.identity("confirmed", "spk-4", S.confirmed(S.d), S.d);
    this.relabelTurnsOf("spk-4");
    this.sendRoster();
    await this.wait(1500, gen);

    // 7. Barge-in: the first speaker interrupts a long answer.
    const t11 = await this.humanTurn(gen, "spk-2", S.t11, { flags: ["addressed_assistant"], hearMs: 1700 });
    await this.reply(gen, t11, S.r11, { bargeIn: { afterSentences: 2, delayMs: 1800 } });
    const t12 = await this.humanTurn(gen, "spk-1", S.t12, { flags: ["barge_in", "addressed_assistant"], hearMs: 1100 });
    await this.reply(gen, t12, S.r12);
    await this.wait(1200, gen);
    this.notice("info", "mock_demo_done", S.demoDone);
  }
}

// ------------------------------------------------------------------ server

const session = new MockSession();

async function readJson(req) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > 64 * 1024) throw new Error("too_large");
    chunks.push(chunk);
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
  } catch {
    return {};
  }
}

function json(res, status, body) {
  res.writeHead(status, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" });
  res.end(JSON.stringify(body));
}

async function handleApi(req, res, pathname) {
  const method = req.method ?? "GET";
  if (pathname === "/api/health" && method === "GET") {
    return json(res, 200, session.health());
  }
  if (pathname === "/api/profiles" && method === "GET") {
    return json(res, 200, session.profiles);
  }
  if (pathname === "/api/profiles" && method === "DELETE") {
    const count = session.profiles.length;
    session.profiles = [];
    for (const speaker of session.speakers.values()) {
      speaker.persistent = false;
      speaker.profileId = null;
    }
    session.sendRoster();
    return json(res, 200, { deleted: count });
  }
  const match = pathname.match(/^\/api\/profiles\/([^/]+)$/);
  if (match && method === "DELETE") {
    const id = decodeURIComponent(match[1]);
    if (!session.profiles.some((profile) => profile.id === id)) return json(res, 404, { detail: "profile_not_found" });
    session.profiles = session.profiles.filter((profile) => profile.id !== id);
    for (const speaker of session.speakers.values()) {
      if (speaker.profileId === id) {
        speaker.persistent = false;
        speaker.profileId = null;
      }
    }
    session.sendRoster();
    return json(res, 200, { deleted: id });
  }
  if (pathname === "/api/settings" && method === "GET") {
    return json(res, 200, session.settings());
  }
  if (pathname === "/api/settings" && method === "PUT") {
    const body = await readJson(req);
    if (["smart", "always", "wake"].includes(body.mode)) session.mode = body.mode;
    if (typeof body.budget_cap_usd === "number" && body.budget_cap_usd >= 0 && body.budget_cap_usd <= 50) {
      session.ledger.cap_usd = body.budget_cap_usd;
    }
    return json(res, 200, session.settings());
  }
  if (pathname === "/api/usage" && method === "GET") {
    return json(res, 200, session.ledger);
  }
  return json(res, 404, { detail: "not_found" });
}

const server = createServer(async (req, res) => {
  try {
    const { pathname } = new URL(req.url ?? "/", "http://localhost");
    if (pathname.startsWith("/api/")) return await handleApi(req, res, pathname);
    if (req.method !== "GET" && req.method !== "HEAD") return res.writeHead(405).end();
    return await serveStatic(pathname, res);
  } catch (error) {
    console.error("[mock] request failed", error);
    if (!res.headersSent) res.writeHead(500);
    res.end();
  }
});

server.on("upgrade", (req, socket, head) => {
  const { pathname } = new URL(req.url ?? "/", "http://localhost");
  const key = req.headers["sec-websocket-key"];
  const isWebSocket = String(req.headers.upgrade ?? "").toLowerCase() === "websocket";
  if (pathname !== "/ws/live" || !isWebSocket || typeof key !== "string") {
    socket.end("HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n");
    return;
  }
  const origin = req.headers.origin;
  if (origin) {
    let host = "";
    try {
      host = new URL(origin).hostname;
    } catch {
      host = "";
    }
    if (!["127.0.0.1", "localhost", "[::1]", "::1"].includes(host)) {
      socket.end("HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n");
      return;
    }
  }
  const accept = createHash("sha1").update(key + WS_GUID).digest("base64");
  socket.write(
    ["HTTP/1.1 101 Switching Protocols", "Upgrade: websocket", "Connection: Upgrade", `Sec-WebSocket-Accept: ${accept}`, "", ""].join("\r\n"),
  );
  session.attach(new MiniSocket(socket, head));
});

server.listen(PORT, HOST, () => {
  console.log(`Utterlane Live mock server: http://${HOST}:${PORT}/`);
  console.log(`  serving ${DIST}`);
  console.log(`  language ${LANG} · brain ${BRAIN}${BRAIN_OFF ? " (listen-only)" : ""}`);
  console.log(`  speed ×${SPEED} · audio ${AUDIO} · ${AUTOSTART ? "autostart" : "demo starts on first mic frame (or after 10 s)"}`);
});

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    server.close();
    process.exit(0);
  });
}
