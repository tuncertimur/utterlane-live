import { useCallback, useEffect, useReducer, useState } from "react";
import type { MicDevice } from "./capture.ts";
import { LiveEngine, type AudioPhase } from "./engine.ts";
import { createInitialState, liveReducer, type LiveState } from "./store.ts";

export interface LiveController {
  state: LiveState;
  engine: LiveEngine;
  audio: AudioPhase;
  devices: MicDevice[];
  deviceId: string | null;
  dismissToast: (id: number) => void;
  notify: (message: string, level?: "info" | "warn" | "error") => void;
}

export function useLive(): LiveController {
  const [state, dispatch] = useReducer(liveReducer, undefined, createInitialState);
  const [audio, setAudio] = useState<AudioPhase>({ phase: "idle" });
  const [devices, setDevices] = useState<MicDevice[]>([]);
  const [deviceId, setDeviceId] = useState<string | null>(null);
  const [engine] = useState(
    () =>
      new LiveEngine({
        dispatch,
        onAudioPhase: setAudio,
        onDevices: (list, selected) => {
          setDevices(list);
          setDeviceId(selected);
        },
      }),
  );

  useEffect(() => {
    engine.connect();
    return () => engine.disconnect();
  }, [engine]);

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") engine.resumeAudio();
    };
    const onDeviceChange = () => void engine.refreshDevices();
    document.addEventListener("visibilitychange", onVisible);
    navigator.mediaDevices?.addEventListener?.("devicechange", onDeviceChange);
    void engine.refreshDevices();
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      navigator.mediaDevices?.removeEventListener?.("devicechange", onDeviceChange);
    };
  }, [engine]);

  const dismissToast = useCallback((id: number) => dispatch({ type: "dismiss_toast", id }), []);
  const notify = useCallback(
    (message: string, level: "info" | "warn" | "error" = "info") =>
      dispatch({ type: "toast", level, message, code: level === "info" ? "local_ok" : "local", at: Date.now() }),
    [],
  );

  return { state, engine, audio, devices, deviceId, dismissToast, notify };
}
