import { memo, useCallback, useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { useI18n } from "../i18n.ts";
import { attributionBadge, chipVariant, flagBadge, formatClock, silentLine, speakerLabel, type Badge } from "../labels.ts";
import type { LevelBus } from "../levels.ts";
import type { Speaker } from "../protocol.ts";
import type { ReplyItem, SilentItem, TimelineItem, TurnItem } from "../store.ts";
import { useLevelVar } from "./hooks.ts";
import { AlertIcon, ArrowDownIcon, InfoIcon, LockIcon, MuteBubbleIcon, ScissorsIcon, SparkIcon } from "./Icons.tsx";

export function speakerColorVar(color: number): string {
  return color >= 0 && color < 8 ? `var(--spk-${color})` : "var(--spk-none)";
}

export function SpeakerChip({ speaker, className = "" }: { speaker: Speaker; className?: string }) {
  const i18n = useI18n();
  const { t } = i18n;
  const variant = chipVariant(speaker);
  const label = speakerLabel(i18n, speaker);
  const style = { "--spk": speakerColorVar(speaker.color) } as CSSProperties;
  const title =
    variant === "unknown"
      ? t("chip.unknown")
      : variant === "provisional"
        ? t("chip.provisional", { name: speaker.name ?? label })
        : variant === "ordinal"
          ? t("chip.ordinal")
          : speaker.persistent
            ? t("chip.saved")
            : t("chip.named");
  return (
    <span className={`chip chip--${variant} ${className}`} style={style} title={title}>
      {variant === "unknown" ? <AlertIcon size={11} className="chip__warn" /> : <span className="chip__dot" aria-hidden="true" />}
      <span className="chip__label">{label}</span>
      {speaker.persistent && variant !== "unknown" && <LockIcon size={10} className="chip__lock" aria-label={t("chip.savedIcon")} />}
    </span>
  );
}

function TurnBadges({ item }: { item: TurnItem }) {
  const i18n = useI18n();
  const { attribution, flags } = item.turn;
  const badges: (Badge & { key: string })[] = [];
  const attr = attributionBadge(i18n, attribution.state);
  if (attr) badges.push({ key: `a-${attribution.state}`, ...attr });
  for (const flag of flags) {
    const badge = flagBadge(i18n, flag);
    if (badge) badges.push({ key: `f-${flag}`, ...badge });
  }
  if (badges.length === 0) return null;
  return (
    <span className="badges">
      {badges.map((badge) => (
        <span key={badge.key} className={`badge badge--${badge.tone}`} title={badge.hint}>
          {badge.label}
        </span>
      ))}
    </span>
  );
}

const TurnBubble = memo(function TurnBubble({ item }: { item: TurnItem }) {
  const i18n = useI18n();
  const { t } = i18n;
  const { turn, relabel } = item;
  const speaker = turn.speaker;
  const style = { "--spk": speakerColorVar(speaker.color) } as CSSProperties;
  const startedMs = turn.started_at !== null ? turn.started_at * 1000 : item.at;
  const { score, margin } = turn.attribution;
  const scoreTitle =
    score === null
      ? undefined
      : margin === null
        ? t("turn.match", { score: score.toFixed(2) })
        : t("turn.matchMargin", { score: score.toFixed(2), margin: margin.toFixed(2) });
  const addressed = turn.flags.includes("addressed_assistant");
  const previous = relabel ? speakerLabel(i18n, relabel.from) : "";
  return (
    <article className="turn" style={style} data-variant={chipVariant(speaker)} data-relabel={relabel ? relabel.seq : undefined}>
      <header className="turn__head">
        <span className="chip-swap" key={relabel?.seq ?? 0} data-animate={relabel ? "" : undefined}>
          {relabel && <SpeakerChip speaker={relabel.from} className="chip--old" />}
          <SpeakerChip speaker={speaker} className="chip--new" />
        </span>
        {relabel && previous !== speakerLabel(i18n, speaker) && (
          <span className="relabel-note" key={`n-${relabel.seq}`} title={t("turn.relabeled")}>
            {i18n.rich("turn.previously", { label: <s>{previous}</s> })}
          </span>
        )}
        <TurnBadges item={item} />
        {addressed && (
          <span className="addressed" title={t("turn.addressed")}>
            <SparkIcon size={12} />
          </span>
        )}
        <time className="stamp" dateTime={new Date(startedMs).toISOString()} title={scoreTitle}>
          {formatClock(startedMs)}
        </time>
      </header>
      <div className="turn__bubble">
        <p>{turn.text || "…"}</p>
      </div>
    </article>
  );
});

function SpeakingBars({ level }: { level: LevelBus }) {
  const ref = useLevelVar<HTMLSpanElement>(level, "--lvl");
  return (
    <span className="speak-bars" ref={ref} aria-hidden="true">
      <i />
      <i />
      <i />
      <i />
    </span>
  );
}

const ReplyBubble = memo(function ReplyBubble({
  item,
  assistant,
  level,
}: {
  item: ReplyItem;
  assistant: string;
  level: LevelBus;
}) {
  const { t } = useI18n();
  const speaking = item.audio === "playing";
  const cut = item.interrupted;
  const usage = item.usage;
  const usageTitle =
    usage && (usage.input_tokens !== null || usage.cost_usd !== null)
      ? `${t("reply.tokens", { input: usage.input_tokens ?? "?", output: usage.output_tokens ?? "?" })}${usage.cost_usd !== null ? ` · $${usage.cost_usd.toFixed(4)}` : ""}`
      : undefined;
  return (
    <article className="reply" data-speaking={speaking || undefined} data-streaming={item.streaming || undefined} data-cut={cut || undefined}>
      <header className="reply__head">
        <time className="stamp" title={usageTitle}>
          {formatClock(item.at)}
        </time>
        {cut && (
          <span className="badge badge--cut" title={t("reply.cutTitle")}>
            <ScissorsIcon size={11} /> {t("reply.cut")}
          </span>
        )}
        {speaking && <SpeakingBars level={level} />}
        <span className="reply__name">{assistant}</span>
        <span className="orb" data-speaking={speaking || undefined} aria-hidden="true" />
      </header>
      <div className="reply__bubble">
        {item.text ? (
          <p>
            {item.text}
            {item.streaming && <span className="caret" aria-hidden="true" />}
            {cut && !item.streaming && <span className="cut-tail" aria-hidden="true">…</span>}
          </p>
        ) : (
          <span className="typing" aria-label={t("reply.typing", { assistant })}>
            <i />
            <i />
            <i />
          </span>
        )}
      </div>
    </article>
  );
});

function SilentLine({ item, assistant, expanded, onToggle }: { item: SilentItem; assistant: string; expanded: boolean; onToggle: () => void }) {
  const i18n = useI18n();
  const text = silentLine(i18n, assistant, item.reason);
  return (
    <div className="silent" data-expanded={expanded || undefined}>
      <button type="button" className="silent__btn" onClick={onToggle} title={expanded ? i18n.t("silent.hide") : text} aria-expanded={expanded}>
        <MuteBubbleIcon size={13} />
        {expanded ? <span>{text}</span> : <span className="sr-only">{text}</span>}
      </button>
    </div>
  );
}

/** Shown once per session when the server says it has no AI brain (`no_brain`). */
function ListenOnlyLine({ onHelp }: { onHelp?: () => void }) {
  const { t } = useI18n();
  return (
    <div className="sysline" role="note">
      <InfoIcon size={13} />
      <span>{t("silent.listenOnly")}</span>
      {onHelp && (
        <>
          {" "}
          <button type="button" className="sysline__link" onClick={onHelp}>
            {t("silent.listenOnlyAction")}
          </button>
        </>
      )}
    </div>
  );
}

function EmptyState({ assistant }: { assistant: string }) {
  const { t } = useI18n();
  return (
    <div className="empty">
      <div className="empty__orb" aria-hidden="true" />
      <h2>{t("empty.title")}</h2>
      <p>{t("empty.body", { assistant })}</p>
      <ul className="empty__hints">
        <li>{t("empty.hint1", { assistant })}</li>
        <li>{t("empty.hint2", { assistant })}</li>
        <li>{t("empty.hint3", { assistant })}</li>
      </ul>
    </div>
  );
}

export interface TimelineProps {
  items: TimelineItem[];
  assistant: string;
  speakLevel: LevelBus;
  showSilent: boolean;
  onToggleSilent: () => void;
  reducedMotion: boolean;
  /** Opens the settings drawer, where listen-only mode is explained. */
  onListenOnlyHelp?: () => void;
}

export function Timeline({
  items,
  assistant,
  speakLevel,
  showSilent,
  onToggleSilent,
  reducedMotion,
  onListenOnlyHelp,
}: TimelineProps) {
  const { t } = useI18n();
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const atBottomRef = useRef(true);
  const [atBottom, setAtBottom] = useState(true);
  const [unseen, setUnseen] = useState(0);
  const lastCount = useRef(items.length);

  const last = items[items.length - 1];
  const signature = `${items.length}:${last ? (last.kind === "reply" ? last.text.length : last.id) : ""}`;

  const scrollToBottom = useCallback(
    (smooth: boolean) => {
      const el = scrollRef.current;
      if (!el) return;
      el.scrollTo({ top: el.scrollHeight, behavior: smooth && !reducedMotion ? "smooth" : "auto" });
    },
    [reducedMotion],
  );

  useLayoutEffect(() => {
    const added = items.length - lastCount.current;
    lastCount.current = items.length;
    if (atBottomRef.current) {
      scrollToBottom(false);
    } else if (added > 0) {
      setUnseen((count) => count + added);
    }
  }, [signature, items.length, scrollToBottom]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = () => {
      const bottom = el.scrollHeight - el.scrollTop - el.clientHeight < 72;
      atBottomRef.current = bottom;
      setAtBottom(bottom);
      if (bottom) setUnseen(0);
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    const resize = new ResizeObserver(() => {
      if (atBottomRef.current) scrollToBottom(false);
    });
    resize.observe(el);
    return () => {
      el.removeEventListener("scroll", onScroll);
      resize.disconnect();
    };
  }, [scrollToBottom]);

  return (
    <section className="timeline-wrap" aria-label={t("timeline.label")}>
      <div className="timeline" ref={scrollRef} role="log" aria-live="polite" aria-relevant="additions">
        <div className="timeline__inner">
          {items.length === 0 ? (
            <EmptyState assistant={assistant} />
          ) : (
            items.map((item) => {
              if (item.kind === "turn") return <TurnBubble key={`t:${item.id}`} item={item} />;
              if (item.kind === "reply")
                return <ReplyBubble key={`r:${item.id}`} item={item} assistant={assistant} level={speakLevel} />;
              if (item.reason === "no_brain") return <ListenOnlyLine key={`s:${item.id}`} onHelp={onListenOnlyHelp} />;
              return (
                <SilentLine
                  key={`s:${item.id}`}
                  item={item}
                  assistant={assistant}
                  expanded={showSilent}
                  onToggle={onToggleSilent}
                />
              );
            })
          )}
        </div>
      </div>
      {!atBottom && (
        <button
          type="button"
          className="jump"
          onClick={() => {
            setUnseen(0);
            scrollToBottom(true);
          }}
        >
          <ArrowDownIcon size={15} />
          {unseen > 0 ? t("timeline.newMessages", { n: unseen }) : t("timeline.jumpLatest")}
        </button>
      )}
    </section>
  );
}
