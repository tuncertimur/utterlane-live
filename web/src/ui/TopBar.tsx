import { useEffect, useRef } from "react";
import { useI18n, type MessageKey } from "../i18n.ts";
import type { LevelBus } from "../levels.ts";
import { modelLabel, statusLabel } from "../labels.ts";
import { MODES, type Mode } from "../protocol.ts";
import type { ConnectionState } from "../store.ts";
import { useLevelVar } from "./hooks.ts";
import {
  MicIcon,
  MicOffIcon,
  MoonIcon,
  PlugIcon,
  SettingsIcon,
  StopIcon,
  SunIcon,
  UsersIcon,
} from "./Icons.tsx";

const CONNECTION_TEXT: Record<ConnectionState, MessageKey> = {
  open: "conn.open",
  idle: "conn.connecting",
  connecting: "conn.connecting",
  reconnecting: "conn.reconnecting",
  replaced: "conn.replaced",
  closed: "conn.closed",
};

export function Wordmark({ connection }: { connection: ConnectionState }) {
  const { t } = useI18n();
  const connectionText = t(CONNECTION_TEXT[connection] ?? "conn.closed");
  return (
    <div className="brand">
      <svg className="brand__mark" viewBox="0 0 32 32" aria-hidden="true">
        <defs>
          <linearGradient id="brandGrad" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="var(--brand-a)" />
            <stop offset="1" stopColor="var(--brand-b)" />
          </linearGradient>
        </defs>
        <rect x="5" y="8" width="14" height="3.6" rx="1.8" fill="url(#brandGrad)" />
        <rect x="10" y="14.2" width="17" height="3.6" rx="1.8" fill="url(#brandGrad)" />
        <rect x="5" y="20.4" width="9" height="3.6" rx="1.8" fill="url(#brandGrad)" opacity="0.7" />
      </svg>
      <div className="brand__text">
        <span className="brand__name">
          Utterlane <span className="brand__live">Live</span>
        </span>
        <span className={`brand__conn brand__conn--${connection}`} role="status">
          <i aria-hidden="true" />
          {connectionText}
        </span>
      </div>
    </div>
  );
}

function PillGlyph({ state, speakLevel }: { state: string; speakLevel: LevelBus }) {
  const waveRef = useLevelVar<HTMLSpanElement>(speakLevel, "--lvl");
  switch (state) {
    case "listening":
      return (
        <span className="glyph glyph--listen" aria-hidden="true">
          <i />
          <i />
          <b />
        </span>
      );
    case "hearing":
      return (
        <span className="glyph glyph--eq" aria-hidden="true">
          <i />
          <i />
          <i />
          <i />
        </span>
      );
    case "thinking":
      return (
        <span className="glyph glyph--dots" aria-hidden="true">
          <i />
          <i />
          <i />
        </span>
      );
    case "speaking":
      return (
        <span className="glyph glyph--wave" aria-hidden="true" ref={waveRef}>
          <i />
          <i />
          <i />
          <i />
          <i />
        </span>
      );
    case "paused":
      return (
        <span className="glyph glyph--pause" aria-hidden="true">
          <i />
          <i />
        </span>
      );
    case "offline":
      return (
        <span className="glyph glyph--offline" aria-hidden="true">
          <PlugIcon size={16} />
        </span>
      );
    default:
      return <span className="glyph glyph--spin" aria-hidden="true" />;
  }
}

export interface StatusAreaProps {
  state: string;
  detail: string | null;
  problems: { key: string; detail: string | null }[];
  speakLevel: LevelBus;
  canStop: boolean;
  onStop: () => void;
}

export function StatusArea({ state, detail, problems, speakLevel, canStop, onStop }: StatusAreaProps) {
  const i18n = useI18n();
  const { t } = i18n;
  const label = statusLabel(i18n, state);
  const problemText = problems
    .map((problem) => `${modelLabel(i18n, problem.key)}: ${problem.detail ?? t("topbar.modelFailed")}`)
    .join(" · ");
  return (
    <div className="status-area">
      <div className="status-row">
        <div className="pill" data-state={state} role="status" aria-live="polite">
          <PillGlyph state={state} speakLevel={speakLevel} />
          <span className="pill__label">{label}</span>
        </div>
        {canStop && (
          <button type="button" className="stop-btn" onClick={onStop} title={t("topbar.stopTitle")}>
            <StopIcon size={16} />
            <span>{t("topbar.stop")}</span>
          </button>
        )}
      </div>
      {problems.length > 0 ? (
        <p className="status-note status-note--error" role="alert">
          {t("topbar.modelError", { problems: problemText })}
        </p>
      ) : detail ? (
        <p className="status-note">{detail}</p>
      ) : null}
    </div>
  );
}

export function MicButton({
  level,
  muted,
  speech,
  disabled,
  onToggle,
}: {
  level: LevelBus;
  muted: boolean;
  speech: boolean;
  disabled: boolean;
  onToggle: () => void;
}) {
  const { t } = useI18n();
  const ref = useRef<HTMLButtonElement | null>(null);
  const arcRef = useRef<SVGCircleElement | null>(null);
  const circumference = 2 * Math.PI * 20;
  useEffect(
    () =>
      level.subscribe((value) => {
        ref.current?.style.setProperty("--mic", value.toFixed(3));
        arcRef.current?.setAttribute("stroke-dashoffset", (circumference * (1 - value)).toFixed(2));
      }),
    [level, circumference],
  );
  return (
    <button
      ref={ref}
      type="button"
      className="mic-btn"
      data-muted={muted || undefined}
      data-speech={(speech && !muted) || undefined}
      onClick={onToggle}
      disabled={disabled}
      aria-pressed={muted}
      aria-label={muted ? t("mic.turnOn") : t("mic.turnOff")}
      title={muted ? t("mic.resumeTitle") : t("mic.pauseTitle")}
    >
      <span className="mic-btn__halo" aria-hidden="true" />
      <svg className="mic-btn__ring" viewBox="0 0 48 48" aria-hidden="true">
        <circle cx="24" cy="24" r="20" className="mic-btn__track" />
        <circle
          ref={arcRef}
          cx="24"
          cy="24"
          r="20"
          className="mic-btn__arc"
          strokeDasharray={circumference.toFixed(2)}
          strokeDashoffset={circumference.toFixed(2)}
        />
      </svg>
      <span className="mic-btn__icon">{muted ? <MicOffIcon size={18} /> : <MicIcon size={18} />}</span>
    </button>
  );
}

export function ModeSwitch({ mode, onChange, disabled }: { mode: Mode; onChange: (mode: Mode) => void; disabled: boolean }) {
  const { t } = useI18n();
  return (
    <>
      <div className="segmented mode-seg" role="radiogroup" aria-label={t("mode.label")}>
        {MODES.map((value) => (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={mode === value}
            className="segmented__opt"
            data-active={mode === value || undefined}
            disabled={disabled}
            onClick={() => onChange(value)}
          >
            {t(`mode.${value}`)}
          </button>
        ))}
      </div>
      <label className="mode-select">
        <span className="sr-only">{t("mode.label")}</span>
        <select value={mode} onChange={(event) => onChange(event.target.value as Mode)} disabled={disabled}>
          {MODES.map((value) => (
            <option key={value} value={value}>
              {t(`mode.${value}`)}
            </option>
          ))}
        </select>
      </label>
    </>
  );
}

export interface TopBarProps {
  connection: ConnectionState;
  status: StatusAreaProps;
  mode: Mode;
  onMode: (mode: Mode) => void;
  muted: boolean;
  speech: boolean;
  micLevel: LevelBus;
  onToggleMute: () => void;
  theme: "dark" | "light";
  onToggleTheme: () => void;
  onOpenSettings: () => void;
  onToggleSidebar: () => void;
  presentCount: number;
}

export function TopBar(props: TopBarProps) {
  const { t } = useI18n();
  const connected = props.connection === "open";
  return (
    <header className="topbar">
      <Wordmark connection={props.connection} />
      <StatusArea {...props.status} />
      <div className="topbar__controls">
        <ModeSwitch mode={props.mode} onChange={props.onMode} disabled={!connected} />
        <MicButton
          level={props.micLevel}
          muted={props.muted}
          speech={props.speech}
          disabled={!connected}
          onToggle={props.onToggleMute}
        />
        <button
          type="button"
          className="icon-btn theme-btn"
          onClick={props.onToggleTheme}
          aria-label={props.theme === "dark" ? t("theme.toLight") : t("theme.toDark")}
          title={props.theme === "dark" ? t("theme.lightTitle") : t("theme.darkTitle")}
        >
          {props.theme === "dark" ? <SunIcon /> : <MoonIcon />}
        </button>
        <button
          type="button"
          className="icon-btn people-btn"
          onClick={props.onToggleSidebar}
          aria-label={t("topbar.people")}
          title={t("topbar.people")}
        >
          <UsersIcon />
          {props.presentCount > 0 && <span className="icon-btn__count">{props.presentCount}</span>}
        </button>
        <button
          type="button"
          className="icon-btn"
          onClick={props.onOpenSettings}
          aria-label={t("topbar.settings")}
          title={t("topbar.settings")}
        >
          <SettingsIcon />
        </button>
      </div>
    </header>
  );
}
