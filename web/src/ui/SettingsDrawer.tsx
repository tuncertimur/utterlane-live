import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { deleteAllProfiles, deleteProfile, fetchHealth, fetchProfiles } from "../api.ts";
import type { MicDevice } from "../capture.ts";
import type { AudioPhase } from "../engine.ts";
import { useI18n, type MessageKey } from "../i18n.ts";
import { brainLabel, formatDate, formatUsd, keyStorageLabel, modelLabel, modelStatusLabel } from "../labels.ts";
import type { ThemePref } from "../prefs.ts";
import { MODES, type Budget, type Health, type Mode, type ModelMap, type Profile } from "../protocol.ts";
import { CloseIcon, ExternalIcon, InfoIcon, LockIcon, RefreshIcon, ReplayIcon, ShieldIcon, TrashIcon } from "./Icons.tsx";

export const GITHUB_URL = "https://github.com/tuncertimur/utterlane-live";

function Section({ title, children, aside }: { title: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="drawer__section">
      <header className="drawer__section-head">
        <h3>{title}</h3>
        {aside}
      </header>
      {children}
    </section>
  );
}

function RefreshButton({ onClick }: { onClick: () => void }) {
  const { t } = useI18n();
  return (
    <button type="button" className="icon-btn icon-btn--small" onClick={onClick} aria-label={t("common.refresh")} title={t("common.refresh")}>
      <RefreshIcon size={15} />
    </button>
  );
}

function isAbort(error: unknown): boolean {
  return (error as { name?: string } | null)?.name === "AbortError";
}

// ---------------------------------------------------------------------------
// Saved voice profiles

type ProfilesState = { status: "loading" } | { status: "error" } | { status: "ready"; profiles: Profile[] };

function ProfilesSection({ open, onChanged }: { open: boolean; onChanged: (message: string) => void }) {
  const i18n = useI18n();
  const { t } = i18n;
  const [state, setState] = useState<ProfilesState>({ status: "loading" });
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [confirmAll, setConfirmAll] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (signal?: AbortSignal) => {
    setState({ status: "loading" });
    try {
      const profiles = await fetchProfiles(signal);
      setState({ status: "ready", profiles });
    } catch (error) {
      if (isAbort(error)) return;
      setState({ status: "error" });
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [open, load]);

  const remove = async (id: string | null) => {
    setBusy(true);
    try {
      if (id) await deleteProfile(id);
      else await deleteAllProfiles();
      onChanged(id ? t("profiles.deleted") : t("profiles.deletedAll"));
    } catch {
      onChanged(t("profiles.deleteFailed"));
    } finally {
      setBusy(false);
      setConfirmId(null);
      setConfirmAll(false);
      void load();
    }
  };

  return (
    <Section title={t("profiles.title")} aside={<RefreshButton onClick={() => void load()} />}>
      <p className="drawer__hint">{t("profiles.hint")}</p>
      {state.status === "loading" && <p className="drawer__muted">{t("common.loading")}</p>}
      {state.status === "error" && <p className="drawer__error">{t("profiles.error")}</p>}
      {state.status === "ready" && state.profiles.length === 0 && <p className="drawer__muted">{t("profiles.none")}</p>}
      {state.status === "ready" && state.profiles.length > 0 && (
        <>
          <ul className="profiles">
            {state.profiles.map((profile) => {
              const name = profile.name || t("profiles.unnamed");
              return (
                <li key={profile.id} className="profiles__row">
                  <span className="profiles__icon" aria-hidden="true">
                    <LockIcon size={14} />
                  </span>
                  <div className="profiles__main">
                    <span className="profiles__name">{name}</span>
                    <span className="profiles__meta">
                      {t("profiles.samples", { n: profile.samples })} ·{" "}
                      {formatDate(profile.updated_at ?? profile.created_at, i18n.locale)}
                    </span>
                  </div>
                  {confirmId === profile.id ? (
                    <div className="profiles__confirm">
                      <button type="button" className="btn btn--small btn--danger" disabled={busy} onClick={() => void remove(profile.id)}>
                        {t("profiles.delete")}
                      </button>
                      <button type="button" className="btn btn--small btn--ghost" onClick={() => setConfirmId(null)}>
                        {t("common.cancel")}
                      </button>
                    </div>
                  ) : (
                    <button
                      type="button"
                      className="icon-btn icon-btn--small"
                      onClick={() => setConfirmId(profile.id)}
                      aria-label={t("profiles.deleteOne", { name })}
                      title={t("profiles.delete")}
                    >
                      <TrashIcon size={15} />
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
          {confirmAll ? (
            <div className="confirm-box" role="alertdialog" aria-label={t("profiles.deleteAllAsk")}>
              <p>
                {i18n.rich("profiles.deleteAllConfirm", {
                  count: <strong>{t("profiles.allCount", { n: state.profiles.length })}</strong>,
                })}
              </p>
              <div className="confirm-box__actions">
                <button type="button" className="btn btn--danger" disabled={busy} onClick={() => void remove(null)}>
                  {t("profiles.deleteAllButton")}
                </button>
                <button type="button" className="btn btn--ghost" onClick={() => setConfirmAll(false)}>
                  {t("common.cancel")}
                </button>
              </div>
            </div>
          ) : (
            <button type="button" className="btn btn--ghost btn--danger-text" onClick={() => setConfirmAll(true)}>
              <TrashIcon size={15} /> {t("profiles.deleteAll")}
            </button>
          )}
        </>
      )}
    </Section>
  );
}

// ---------------------------------------------------------------------------
// About: models, hardware, voice attribution, brain, key storage (GET /api/health)

type HealthState = { status: "loading" } | { status: "error" } | { status: "ready"; health: Health };

function useHealth(open: boolean): { state: HealthState; reload: () => void } {
  const [state, setState] = useState<HealthState>({ status: "loading" });
  const load = useCallback(async (signal?: AbortSignal) => {
    // Keep showing the last answer while refreshing, so the panel does not flicker.
    setState((previous) => (previous.status === "ready" ? previous : { status: "loading" }));
    try {
      const health = await fetchHealth(signal);
      setState(health ? { status: "ready", health } : { status: "error" });
    } catch (error) {
      if (isAbort(error)) return;
      setState({ status: "error" });
    }
  }, []);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [open, load]);
  const reload = useCallback(() => void load(), [load]);
  return { state, reload };
}

const MODEL_ORDER = ["stt", "tts", "llm", "embedding", "vad"];

function AboutSection({
  health,
  models,
  brain,
  rttMs,
  onRefresh,
}: {
  health: HealthState;
  models: ModelMap;
  brain: string | null;
  rttMs: number | null;
  onRefresh: () => void;
}) {
  const i18n = useI18n();
  const { t } = i18n;
  const info = health.status === "ready" ? health.health : null;
  // Live statuses from `ready` win; /api/health fills in before the socket is up.
  const shown = Object.keys(models).length > 0 ? models : (info?.models ?? {});
  const keys = [
    ...MODEL_ORDER.filter((key) => key in shown),
    ...Object.keys(shown).filter((key) => !MODEL_ORDER.includes(key)),
  ];

  let note: { key: MessageKey; error?: boolean } | null = null;
  if (health.status === "error") note = { key: "about.error", error: true };
  else if (info?.status === "loading") note = { key: "about.serverLoading" };
  else if (keys.length === 0) note = { key: health.status === "loading" ? "common.loading" : "about.waiting" };

  return (
    <Section title={t("about.title")} aside={<RefreshButton onClick={onRefresh} />}>
      {keys.length > 0 && (
        <ul className="models">
          {keys.map((key) => {
            const model = shown[key];
            const kind =
              key === "llm" ? (model.status === "off" ? t("brain.off") : brain ? brainLabel(i18n, brain) : null) : null;
            // The voice's license attribution (CC-BY) must stay visible; errors explain themselves.
            const detail = model.detail && (key === "tts" || model.status === "error") ? model.detail : null;
            return (
              <li key={key} className="models__row" data-status={model.status}>
                <span className="models__dot" aria-hidden="true" />
                <div className="models__main">
                  <div className="models__top">
                    <span className="models__name">{modelLabel(i18n, key)}</span>
                    <span className="models__status">{modelStatusLabel(i18n, model.status)}</span>
                  </div>
                  {kind && <span className="models__kind">{kind}</span>}
                  <span className="models__id" title={detail ? undefined : (model.detail ?? undefined)}>
                    {model.id}
                  </span>
                  {detail && <span className="models__detail">{detail}</span>}
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {note && <p className={note.error ? "drawer__error" : "drawer__muted"}>{t(note.key)}</p>}
      <dl className="facts">
        {info?.hardware && (
          <div className="facts__row">
            <dt>{t("about.hardware")}</dt>
            <dd>{info.hardware}</dd>
          </div>
        )}
        {info?.profiles_key_storage && (
          <div className="facts__row">
            <dt>{t("about.keyStorage")}</dt>
            <dd>{keyStorageLabel(i18n, info.profiles_key_storage)}</dd>
          </div>
        )}
        {info?.version && (
          <div className="facts__row">
            <dt>{t("about.version")}</dt>
            <dd>{info.version}</dd>
          </div>
        )}
        {rttMs !== null && (
          <div className="facts__row">
            <dt>{t("about.latency")}</dt>
            <dd>{t("about.latencyValue", { ms: rttMs })}</dd>
          </div>
        )}
      </dl>
    </Section>
  );
}

// ---------------------------------------------------------------------------

export interface SettingsDrawerProps {
  open: boolean;
  onClose: () => void;
  devices: MicDevice[];
  deviceId: string | null;
  onDevice: (id: string | null) => void;
  audio: AudioPhase;
  mode: Mode;
  onMode: (mode: Mode) => void;
  connected: boolean;
  /** True once the language comes from the server rather than the browser. */
  languageFromServer: boolean;
  /** `brain` from `GET /api/settings`, null until known. */
  brain: string | null;
  /** No AI brain: explain how to add one. */
  listenOnly: boolean;
  themePref: ThemePref;
  onTheme: (pref: ThemePref) => void;
  showSilent: boolean;
  onShowSilent: (value: boolean) => void;
  budget: Budget;
  models: ModelMap;
  assistant: string;
  rttMs: number | null;
  onResetSession: () => void;
  onNotify: (message: string) => void;
}

const THEMES: readonly (readonly [ThemePref, MessageKey])[] = [
  ["system", "settings.themeSystem"],
  ["dark", "settings.themeDark"],
  ["light", "settings.themeLight"],
];

export function SettingsDrawer(props: SettingsDrawerProps) {
  const { open, onClose } = props;
  const i18n = useI18n();
  const { t } = i18n;
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const returnFocus = useRef<Element | null>(null);
  const [confirmReset, setConfirmReset] = useState(false);
  const health = useHealth(open);

  useEffect(() => {
    if (!open) return;
    returnFocus.current = document.activeElement;
    closeRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      setConfirmReset(false);
      (returnFocus.current as HTMLElement | null)?.focus?.();
    };
  }, [open, onClose]);

  const ratio = props.budget.cap_usd > 0 ? Math.min(1, props.budget.spent_usd / props.budget.cap_usd) : 0;
  const audio = props.audio;
  const info = health.state.status === "ready" ? health.state.health : null;
  const code = (text: string) => <code>{text}</code>;

  return (
    <>
      <div className="drawer-backdrop" data-open={open || undefined} onClick={onClose} aria-hidden="true" />
      <div
        className="drawer"
        data-open={open || undefined}
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-title"
        aria-hidden={!open}
        inert={!open}
      >
        <header className="drawer__head">
          <h2 id="settings-title">{t("settings.title")}</h2>
          <button ref={closeRef} type="button" className="icon-btn" onClick={onClose} aria-label={t("settings.close")}>
            <CloseIcon />
          </button>
        </header>
        <div className="drawer__body">
          {props.listenOnly && (
            <Section title={t("brain.title")}>
              <div className="callout" role="note">
                <InfoIcon size={20} />
                <div className="callout__body">
                  <strong>{t("brain.listenOnlyTitle")}</strong>
                  <p>{t("brain.listenOnlyBody", { assistant: props.assistant })}</p>
                  <p>
                    {i18n.rich("brain.howTo", {
                      openaiKey: code("OPENAI_API_KEY"),
                      file: code(".env"),
                      chat: code("UTTERLANE_BRAIN=ollama"),
                      docs: code("docs/INTEGRATIONS.md"),
                    })}
                  </p>
                  <p className="callout__note">{t("brain.restart")}</p>
                </div>
              </div>
            </Section>
          )}

          <Section title={t("settings.mic")}>
            <label className="field">
              <span className="field__label">{t("settings.inputDevice")}</span>
              <select value={props.deviceId ?? ""} onChange={(event) => props.onDevice(event.target.value || null)}>
                <option value="">{t("mic.default")}</option>
                {props.devices
                  .filter((device) => device.deviceId && device.deviceId !== "default")
                  .map((device, index) => (
                    <option key={device.deviceId} value={device.deviceId}>
                      {device.label || t("mic.numbered", { n: index + 1 })}
                    </option>
                  ))}
              </select>
            </label>
            <p className="drawer__hint">
              {audio.phase === "running"
                ? t("settings.micActive", {
                    mic: audio.micLabel || t("mic.generic"),
                    rate: audio.resampling
                      ? t("settings.micResampled", { khz: Math.round(audio.sampleRate / 1000) })
                      : t("settings.micNative"),
                  })
                : audio.phase === "listen_only"
                  ? t("settings.micListenOnly")
                  : t("settings.micHint")}
            </p>
          </Section>

          <Section title={t("mode.label")}>
            <div className="mode-cards" role="radiogroup" aria-label={t("mode.label")}>
              {MODES.map((value) => (
                <button
                  key={value}
                  type="button"
                  role="radio"
                  aria-checked={props.mode === value}
                  className="mode-card"
                  data-active={props.mode === value || undefined}
                  disabled={!props.connected}
                  onClick={() => props.onMode(value)}
                >
                  <span className="mode-card__radio" aria-hidden="true" />
                  <span className="mode-card__text">
                    <strong>{t(`mode.${value}`)}</strong>
                    <span>{t(`mode.${value}.desc`, { assistant: props.assistant })}</span>
                  </span>
                </button>
              ))}
            </div>
          </Section>

          <Section title={t("settings.language")}>
            <p className="lang-row">
              <strong>{t("settings.languageName")}</strong>{" "}
              <span className="lang-row__source">
                {props.languageFromServer ? t("settings.languageServer") : t("settings.languageBrowser")}
              </span>
            </p>
            <p className="drawer__hint">
              {i18n.rich("settings.languageHint", {
                cli: code("utterlane --language en|tr"),
                env: code("UTTERLANE_LANGUAGE"),
                file: code(".env"),
              })}
            </p>
          </Section>

          <Section title={t("settings.appearance")}>
            <div className="field">
              <span className="field__label">{t("settings.theme")}</span>
              <div className="segmented" role="radiogroup" aria-label={t("settings.theme")}>
                {THEMES.map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    role="radio"
                    aria-checked={props.themePref === value}
                    className="segmented__opt"
                    data-active={props.themePref === value || undefined}
                    onClick={() => props.onTheme(value)}
                  >
                    {t(label)}
                  </button>
                ))}
              </div>
            </div>
            <label className="switch">
              <input type="checkbox" checked={props.showSilent} onChange={(event) => props.onShowSilent(event.target.checked)} />
              <span className="switch__track" aria-hidden="true" />
              <span>{t("settings.showSilent", { assistant: props.assistant })}</span>
            </label>
          </Section>

          <ProfilesSection open={open} onChanged={props.onNotify} />

          <Section title={t("budget.title")}>
            <div className="budget">
              <div className="budget__nums">
                <span className="budget__spent">{formatUsd(props.budget.spent_usd)}</span>
                <span className="budget__cap">
                  / {props.budget.cap_usd > 0 ? formatUsd(props.budget.cap_usd) : t("budget.unlimited")}
                </span>
              </div>
              <div className="usage__bar" data-tone={ratio >= 0.95 ? "danger" : ratio >= 0.75 ? "warn" : "ok"}>
                <i style={{ width: `${(ratio * 100).toFixed(1)}%` }} />
              </div>
              <p className="drawer__hint">{t("budget.hint", { n: props.budget.calls, assistant: props.assistant })}</p>
            </div>
          </Section>

          <AboutSection
            health={health.state}
            models={props.models}
            brain={props.brain}
            rttMs={props.rttMs}
            onRefresh={health.reload}
          />

          <Section title={t("privacy.title")}>
            <div className="privacy">
              <ShieldIcon size={22} />
              <ul>
                <li>{t("privacy.local")}</li>
                <li>{t("privacy.consent")}</li>
                <li>{info?.profiles_key_storage === "file" ? t("privacy.encryptedFile") : t("privacy.encryptedOs")}</li>
                <li>{t("privacy.textOnly")}</li>
                <li>{t("privacy.label")}</li>
              </ul>
            </div>
          </Section>

          <Section title={t("session.title")}>
            {confirmReset ? (
              <div className="confirm-box" role="alertdialog" aria-label={t("session.resetAsk")}>
                <p>{t("session.resetBody")}</p>
                <div className="confirm-box__actions">
                  <button
                    type="button"
                    className="btn btn--danger"
                    onClick={() => {
                      props.onResetSession();
                      setConfirmReset(false);
                      onClose();
                    }}
                  >
                    {t("session.resetConfirm")}
                  </button>
                  <button type="button" className="btn btn--ghost" onClick={() => setConfirmReset(false)}>
                    {t("common.cancel")}
                  </button>
                </div>
              </div>
            ) : (
              <button type="button" className="btn btn--ghost" disabled={!props.connected} onClick={() => setConfirmReset(true)}>
                <ReplayIcon size={15} /> {t("session.reset")}
              </button>
            )}
          </Section>

          <footer className="drawer__foot">
            <span>Utterlane Live{info?.version ? ` ${info.version}` : ""}</span>
            <span aria-hidden="true">·</span>
            <a href={GITHUB_URL} target="_blank" rel="noopener" title={t("about.githubTitle")}>
              {t("about.github")}
              <ExternalIcon size={13} />
            </a>
          </footer>
        </div>
      </div>
    </>
  );
}
