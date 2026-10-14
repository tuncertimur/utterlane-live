import { useEffect, useRef, useState } from "react";
import { queryMicPermission, type MicPermission } from "../capture.ts";
import type { AudioPhase } from "../engine.ts";
import { useI18n, type MessageKey } from "../i18n.ts";
import { captureError } from "../labels.ts";
import type { ConnectionState } from "../store.ts";
import { AlertIcon, MicIcon, ShieldIcon } from "./Icons.tsx";

export interface StartOverlayProps {
  audio: AudioPhase;
  connection: ConnectionState;
  assistant: string;
  onStart: () => void;
  onListenOnly: () => void;
}

const SERVER_TEXT: Partial<Record<ConnectionState, MessageKey>> = {
  open: "start.serverOpen",
  reconnecting: "start.serverRetrying",
  replaced: "start.serverReplaced",
};

/** Where the microphone works without https: this page's port on the loopback address. */
function loopbackUrl(): string {
  return `http://127.0.0.1:${window.location.port || "8765"}`;
}

export function StartOverlay({ audio, connection, assistant, onStart, onListenOnly }: StartOverlayProps) {
  const i18n = useI18n();
  const { t } = i18n;
  const [permission, setPermission] = useState<MicPermission>("unknown");
  const buttonRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    let alive = true;
    void queryMicPermission().then((state) => {
      if (alive) setPermission(state);
    });
    buttonRef.current?.focus();
    return () => {
      alive = false;
    };
  }, []);

  const starting = audio.phase === "starting";
  const error = audio.phase === "error" ? captureError(i18n, audio.kind, loopbackUrl()) : null;
  const serverText = t(SERVER_TEXT[connection] ?? "start.serverConnecting");

  return (
    <div className="overlay" role="dialog" aria-modal="true" aria-labelledby="start-title">
      <div className="welcome">
        <div className="welcome__orb" aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
        <p className="welcome__eyebrow" lang="en">
          Utterlane Live
        </p>
        <h1 id="start-title">{t("start.title")}</h1>
        <p className="welcome__lead">{t("start.lead")}</p>
        <p className="welcome__privacy">
          <ShieldIcon size={16} />
          <span>{t("start.privacy")}</span>
        </p>

        {error && (
          <div className="welcome__error" role="alert">
            <AlertIcon size={18} />
            <div>
              <strong>{error.title}</strong>
              <p>{error.body}</p>
            </div>
          </div>
        )}

        <button ref={buttonRef} type="button" className="btn btn--primary btn--hero" onClick={onStart} disabled={starting}>
          {starting ? <span className="btn__spinner" aria-hidden="true" /> : <MicIcon size={20} />}
          {starting ? t("start.opening") : error ? t("start.retry") : t("start.start")}
        </button>

        {error && (
          <button type="button" className="btn btn--ghost welcome__alt" onClick={onListenOnly}>
            {t("start.withoutMic")}
          </button>
        )}

        <div className="welcome__foot">
          <span className={`welcome__server welcome__server--${connection}`}>
            <i aria-hidden="true" />
            {serverText}
          </span>
          {!error && (
            <span className="welcome__perm">
              {permission === "granted"
                ? t("start.permGranted")
                : permission === "denied"
                  ? t("start.permDenied")
                  : t("start.permPrompt", { assistant })}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
