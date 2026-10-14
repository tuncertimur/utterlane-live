import { useCallback, useRef, useState, type CSSProperties, type FormEvent } from "react";
import { useI18n } from "../i18n.ts";
import { formatClock, formatUsd, identityKindLabel, nameStateLabel, speakerLabel } from "../labels.ts";
import type { Budget, Speaker } from "../protocol.ts";
import type { IdentityLogEntry } from "../store.ts";
import { useDismiss } from "./hooks.ts";
import { CloseIcon, EraseIcon, LockIcon, MoreIcon, PencilIcon } from "./Icons.tsx";
import { speakerColorVar } from "./Timeline.tsx";

function RosterRow({
  speaker,
  onRename,
  onForget,
  disabled,
}: {
  speaker: Speaker;
  onRename: (id: string, name: string) => void;
  onForget: (id: string) => void;
  disabled: boolean;
}) {
  const i18n = useI18n();
  const { t } = i18n;
  const [menu, setMenu] = useState(false);
  const [mode, setMode] = useState<"view" | "rename" | "forget">("view");
  const [draft, setDraft] = useState("");
  const menuRef = useRef<HTMLDivElement | null>(null);
  const closeMenu = useCallback(() => setMenu(false), []);
  useDismiss(menu, menuRef, closeMenu);

  const style = { "--spk": speakerColorVar(speaker.color) } as CSSProperties;
  const nameState = nameStateLabel(i18n, speaker.name_state);
  const label = speakerLabel(i18n, speaker);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    onRename(speaker.id, draft);
    setMode("view");
  };

  return (
    <li
      className="roster__row"
      style={style}
      data-away={!speaker.present || undefined}
      data-state={speaker.name_state}
      data-menu={menu || undefined}
    >
      <span className="roster__dot" aria-hidden="true" />
      {mode === "rename" ? (
        <form className="roster__rename" onSubmit={submit}>
          <input
            autoFocus
            value={draft}
            maxLength={40}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape") setMode("view");
            }}
            placeholder={speaker.ordinal ? t("speaker.person", { n: speaker.ordinal }) : t("roster.namePlaceholder")}
            aria-label={t("roster.newName")}
          />
          <button type="submit" className="btn btn--small btn--primary">
            {t("roster.save")}
          </button>
          <button type="button" className="icon-btn icon-btn--small" onClick={() => setMode("view")} aria-label={t("common.cancel")}>
            <CloseIcon size={14} />
          </button>
        </form>
      ) : mode === "forget" ? (
        <div className="roster__confirm" role="alertdialog" aria-label={t("roster.forgetAsk", { name: label })}>
          <span>
            {i18n.rich("roster.forgetAsk", { name: <strong>{label}</strong> })}
            {speaker.persistent ? ` ${t("roster.forgetProfileToo")}` : ""}
          </span>
          <div className="roster__confirm-actions">
            <button
              type="button"
              className="btn btn--small btn--danger"
              onClick={() => {
                onForget(speaker.id);
                setMode("view");
              }}
            >
              {t("roster.forget")}
            </button>
            <button type="button" className="btn btn--small btn--ghost" onClick={() => setMode("view")}>
              {t("common.cancel")}
            </button>
          </div>
        </div>
      ) : (
        <>
          <div className="roster__main">
            <span className="roster__name">
              {label}
              {speaker.persistent && (
                <span className="roster__lock" title={t("roster.savedTitle")}>
                  <LockIcon size={12} />
                </span>
              )}
            </span>
            <span className="roster__meta">
              <span className={`roster__state roster__state--${speaker.name_state}`}>{nameState}</span>
              <span aria-hidden="true">·</span>
              <span>{t("roster.turns", { n: speaker.turns })}</span>
              {!speaker.present && (
                <>
                  <span aria-hidden="true">·</span>
                  <span>{t("roster.away")}</span>
                </>
              )}
            </span>
          </div>
          <div className="roster__menu" ref={menuRef}>
            <button
              type="button"
              className="icon-btn icon-btn--small"
              aria-haspopup="menu"
              aria-expanded={menu}
              aria-label={t("roster.options", { name: label })}
              onClick={() => setMenu((open) => !open)}
              disabled={disabled}
            >
              <MoreIcon size={16} />
            </button>
            {menu && (
              <div className="menu" role="menu">
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setDraft(speaker.name ?? "");
                    setMode("rename");
                    setMenu(false);
                  }}
                >
                  <PencilIcon size={15} /> {t("roster.rename")}
                </button>
                <button
                  type="button"
                  role="menuitem"
                  className="menu__danger"
                  onClick={() => {
                    setMode("forget");
                    setMenu(false);
                  }}
                >
                  <EraseIcon size={15} /> {t("roster.forget")}
                </button>
              </div>
            )}
          </div>
        </>
      )}
    </li>
  );
}

function UsageMeter({ budget }: { budget: Budget }) {
  const { t } = useI18n();
  const ratio = budget.cap_usd > 0 ? Math.min(1, budget.spent_usd / budget.cap_usd) : 0;
  const tone = ratio >= 0.95 ? "danger" : ratio >= 0.75 ? "warn" : "ok";
  return (
    <div className="usage" data-tone={tone}>
      <div className="usage__row">
        <span>{t("usage.title")}</span>
        <span className="usage__value">
          {formatUsd(budget.spent_usd)} <span className="usage__cap">/ {budget.cap_usd > 0 ? formatUsd(budget.cap_usd) : "—"}</span>
        </span>
      </div>
      <div
        className="usage__bar"
        role="progressbar"
        aria-label={t("usage.aria")}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(ratio * 100)}
      >
        <i style={{ width: `${(ratio * 100).toFixed(1)}%` }} />
      </div>
    </div>
  );
}

export interface SidebarProps {
  speakers: Speaker[];
  identityLog: IdentityLogEntry[];
  budget: Budget;
  open: boolean;
  connected: boolean;
  onClose: () => void;
  onRename: (id: string, name: string) => void;
  onForget: (id: string) => void;
}

export function Sidebar({ speakers, identityLog, budget, open, connected, onClose, onRename, onForget }: SidebarProps) {
  const i18n = useI18n();
  const { t } = i18n;
  const present = speakers.filter((speaker) => speaker.present).length;
  const byId = new Map(speakers.map((speaker) => [speaker.id, speaker]));
  return (
    <>
      <div className="sidebar-backdrop" data-open={open || undefined} onClick={onClose} aria-hidden="true" />
      <aside className="sidebar" data-open={open || undefined} aria-label={t("sidebar.label")}>
        <div className="sidebar__scroll">
          <section className="panel">
            <header className="panel__head">
              <h2>{t("sidebar.title")}</h2>
              <span className="panel__count">{present > 0 ? t("sidebar.present", { n: present }) : t("sidebar.emptyCount")}</span>
              <button type="button" className="icon-btn icon-btn--small sidebar__close" onClick={onClose} aria-label={t("sidebar.close")}>
                <CloseIcon size={16} />
              </button>
            </header>
            {speakers.length === 0 ? (
              <p className="panel__empty">{t("sidebar.noSpeakers")}</p>
            ) : (
              <ul className="roster">
                {speakers.map((speaker) => (
                  <RosterRow key={speaker.id} speaker={speaker} onRename={onRename} onForget={onForget} disabled={!connected} />
                ))}
              </ul>
            )}
          </section>
          <section className="panel panel--log">
            <header className="panel__head">
              <h2>{t("sidebar.events")}</h2>
              {identityLog.length > 0 && <span className="panel__count">{identityLog.length}</span>}
            </header>
            {identityLog.length === 0 ? (
              <p className="panel__empty">{t("sidebar.noEvents")}</p>
            ) : (
              <ol className="idlog">
                {identityLog.map((entry) => {
                  const speaker = entry.speakerId ? byId.get(entry.speakerId) : undefined;
                  const style = { "--spk": speakerColorVar(speaker?.color ?? -1) } as CSSProperties;
                  return (
                    <li key={entry.id} className="idlog__row" data-kind={entry.kind} style={style}>
                      <span className="idlog__dot" aria-hidden="true" />
                      <div className="idlog__body">
                        <div className="idlog__top">
                          <span className="idlog__kind">{identityKindLabel(i18n, entry.kind)}</span>
                          <time className="idlog__time">{formatClock(entry.at, true)}</time>
                        </div>
                        <p className="idlog__msg">{entry.message || entry.name || "—"}</p>
                      </div>
                    </li>
                  );
                })}
              </ol>
            )}
          </section>
        </div>
        <footer className="sidebar__foot">
          <UsageMeter budget={budget} />
        </footer>
      </aside>
    </>
  );
}
