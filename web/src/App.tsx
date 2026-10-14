import { useCallback, useEffect, useState } from "react";
import { fetchSettings } from "./api.ts";
import { I18nContext, browserLanguage, resolveLanguage, translator, type Translator } from "./i18n.ts";
import { statusLabel } from "./labels.ts";
import { prefs, type ThemePref } from "./prefs.ts";
import type { Mode, ServerSettings } from "./protocol.ts";
import { effectiveStatus, isListenOnly, lastReplyPlaying, modelProblems, sortRoster } from "./store.ts";
import { useReducedMotion } from "./ui/hooks.ts";
import { PlugIcon, RefreshIcon } from "./ui/Icons.tsx";
import { SettingsDrawer } from "./ui/SettingsDrawer.tsx";
import { Sidebar } from "./ui/Sidebar.tsx";
import { StartOverlay } from "./ui/StartOverlay.tsx";
import { Timeline } from "./ui/Timeline.tsx";
import { Toasts } from "./ui/Toasts.tsx";
import { TopBar } from "./ui/TopBar.tsx";
import { useLive } from "./useLive.ts";

type Theme = "dark" | "light";

function resolveTheme(pref: ThemePref): Theme {
  if (pref !== "system") return pref;
  try {
    return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  } catch {
    return "dark";
  }
}

function useTheme() {
  const [pref, setPref] = useState<ThemePref>(() => prefs.theme());
  const [resolved, setResolved] = useState<Theme>(() => resolveTheme(pref));
  useEffect(() => {
    const apply = () => {
      const theme = resolveTheme(pref);
      setResolved(theme);
      document.documentElement.setAttribute("data-theme", theme);
      document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "dark" ? "#0b0d12" : "#f5f6fa");
    };
    apply();
    if (pref !== "system") return;
    let media: MediaQueryList;
    try {
      media = window.matchMedia("(prefers-color-scheme: light)");
    } catch {
      return;
    }
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, [pref]);
  const choose = useCallback((value: ThemePref) => {
    prefs.setTheme(value);
    setPref(value);
  }, []);
  return { pref, resolved, choose };
}

/**
 * `GET /api/settings` (conversation language, AI brain), refreshed on every (re)connect:
 * a restarted server may run with another `--language` or brain.
 */
function useServerSettings(connected: boolean, sessionId: string | null): ServerSettings | null {
  const [settings, setSettings] = useState<ServerSettings | null>(null);
  useEffect(() => {
    if (!connected) return;
    const controller = new AbortController();
    fetchSettings(controller.signal).then(
      (value) => {
        if (value) setSettings(value);
      },
      () => undefined, // Optional: `ready` carries the language too.
    );
    return () => controller.abort();
  }, [connected, sessionId]);
  return settings;
}

function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el) return false;
  return el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName);
}

function dockDetail(i18n: Translator, mode: Mode, presentCount: number, assistant: string): string {
  if (mode === "smart") {
    return presentCount > 1
      ? i18n.t("dock.smartGroup", { n: presentCount, assistant })
      : i18n.t("dock.smartSolo", { assistant });
  }
  return mode === "wake" ? i18n.t("dock.wake", { assistant }) : i18n.t("dock.always");
}

export default function App() {
  const live = useLive();
  const { state, engine, audio } = live;
  const theme = useTheme();
  const reducedMotion = useReducedMotion();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [showSilent, setShowSilent] = useState(() => prefs.showSilent());
  const [browserLang] = useState(() => browserLanguage(globalThis.navigator?.language));

  const connected = state.connection === "open";
  const serverSettings = useServerSettings(connected, state.sessionId);
  const language = resolveLanguage({ ready: state.language, settings: serverSettings?.language, browser: browserLang });
  const i18n = translator(language.lang);
  const { t } = i18n;
  const listenOnly = isListenOnly(state, serverSettings?.brain ?? null);
  const pillState = connected ? effectiveStatus(state) : "offline";
  const canStop = lastReplyPlaying(state) !== null;
  const roster = sortRoster(state.speakers);
  const presentCount = roster.filter((speaker) => speaker.present).length;
  const showOverlay = audio.phase === "idle" || audio.phase === "starting" || audio.phase === "error";
  const muted = !state.listening;

  const toggleSilent = useCallback(() => {
    setShowSilent((value) => {
      prefs.setShowSilent(!value);
      return !value;
    });
  }, []);
  const setSilent = useCallback((value: boolean) => {
    prefs.setShowSilent(value);
    setShowSilent(value);
  }, []);
  const closeSettings = useCallback(() => setSettingsOpen(false), []);
  const openSettings = useCallback(() => setSettingsOpen(true), []);

  useEffect(() => {
    document.documentElement.lang = language.lang;
  }, [language.lang]);

  useEffect(() => {
    document.title = i18n.t("app.documentTitle", { status: statusLabel(i18n, pillState) });
  }, [i18n, pillState]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey || isTyping(event.target)) return;
      if (event.key === "Escape" && !settingsOpen && engine.isSpeaking) {
        engine.stopSpeaking();
      } else if ((event.key === "m" || event.key === "M") && connected && !showOverlay) {
        engine.setListening(!state.listening);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [engine, settingsOpen, connected, showOverlay, state.listening]);

  return (
    <I18nContext value={i18n}>
      <div className="app" data-overlay={showOverlay || undefined}>
        <TopBar
          connection={state.connection}
          status={{
            state: pillState,
            detail: connected ? state.status.detail : null,
            problems: modelProblems(state.models),
            speakLevel: engine.speakLevel,
            canStop,
            onStop: () => engine.stopSpeaking(),
          }}
          mode={state.mode}
          onMode={(mode) => engine.setMode(mode)}
          muted={muted}
          speech={state.speech}
          micLevel={engine.micLevel}
          onToggleMute={() => engine.setListening(muted)}
          theme={theme.resolved}
          onToggleTheme={() => theme.choose(theme.resolved === "dark" ? "light" : "dark")}
          onOpenSettings={openSettings}
          onToggleSidebar={() => setSidebarOpen((open) => !open)}
          presentCount={presentCount}
        />

        {state.connection === "replaced" ? (
          <div className="banner" role="status">
            <PlugIcon size={16} />
            <span>{t("app.replaced")}</span>
            <button type="button" className="btn btn--small btn--ghost" onClick={() => engine.takeOver()}>
              {t("app.continueHere")}
            </button>
          </div>
        ) : state.connectedOnce && !connected && (
          <div className="banner" role="status">
            <PlugIcon size={16} />
            <span>
              {state.retryInMs !== null
                ? t("app.reconnectingIn", { s: Math.max(1, Math.round(state.retryInMs / 1000)) })
                : t("app.reconnecting")}
            </span>
            <button type="button" className="btn btn--small btn--ghost" onClick={() => engine.retryNow()}>
              <RefreshIcon size={14} /> {t("app.retryNow")}
            </button>
          </div>
        )}

        <div className="layout">
          <main className="stage">
            <Timeline
              items={state.items}
              assistant={state.assistantName}
              speakLevel={engine.speakLevel}
              showSilent={showSilent}
              onToggleSilent={toggleSilent}
              reducedMotion={reducedMotion}
              onListenOnlyHelp={openSettings}
            />
            <footer className="dock">
              <span className="dock__mode">
                {listenOnly ? (
                  <>
                    <strong>{t("dock.listenOnly")}</strong> · {t("dock.listenOnlyDetail")}
                  </>
                ) : (
                  <>
                    <strong>{t(`mode.${state.mode}`)}</strong> · {dockDetail(i18n, state.mode, presentCount, state.assistantName)}
                  </>
                )}
              </span>
              {audio.phase === "listen_only" ? (
                <span className="dock__mic dock__mic--off">{t("dock.noMic")}</span>
              ) : audio.phase === "running" ? (
                <span className="dock__mic">{muted ? t("dock.micOff") : audio.micLabel || t("mic.generic")}</span>
              ) : null}
            </footer>
          </main>
          <Sidebar
            speakers={roster}
            identityLog={state.identityLog}
            budget={state.budget}
            open={sidebarOpen}
            connected={connected}
            onClose={() => setSidebarOpen(false)}
            onRename={(id, name) => engine.renameSpeaker(id, name)}
            onForget={(id) => engine.forgetSpeaker(id)}
          />
        </div>

        <SettingsDrawer
          open={settingsOpen}
          onClose={closeSettings}
          devices={live.devices}
          deviceId={live.deviceId}
          onDevice={(id) => void engine.setDevice(id)}
          audio={audio}
          mode={state.mode}
          onMode={(mode) => engine.setMode(mode)}
          connected={connected}
          languageFromServer={language.fromServer}
          brain={serverSettings?.brain ?? null}
          listenOnly={listenOnly}
          themePref={theme.pref}
          onTheme={theme.choose}
          showSilent={showSilent}
          onShowSilent={setSilent}
          budget={state.budget}
          models={state.models}
          assistant={state.assistantName}
          rttMs={state.rttMs}
          onResetSession={() => engine.resetSession()}
          onNotify={(message) => live.notify(message)}
        />

        {showOverlay && (
          <StartOverlay
            audio={audio}
            connection={state.connection}
            assistant={state.assistantName}
            onStart={() => void engine.start()}
            onListenOnly={() => engine.startListenOnly()}
          />
        )}

        <Toasts toasts={state.toasts} onDismiss={live.dismissToast} />
      </div>
    </I18nContext>
  );
}
