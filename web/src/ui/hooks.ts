import { useEffect, useRef, useState, type RefObject } from "react";
import type { LevelBus } from "../levels.ts";

/** Write a LevelBus value into a CSS custom property on an element, without re-rendering. */
export function useLevelVar<T extends HTMLElement>(bus: LevelBus, name: string): RefObject<T | null> {
  const ref = useRef<T | null>(null);
  useEffect(
    () =>
      bus.subscribe((value) => {
        ref.current?.style.setProperty(name, value.toFixed(3));
      }),
    [bus, name],
  );
  return ref;
}

export function useReducedMotion(): boolean {
  const query = "(prefers-reduced-motion: reduce)";
  const [reduced, setReduced] = useState(() => {
    try {
      return window.matchMedia(query).matches;
    } catch {
      return false;
    }
  });
  useEffect(() => {
    let media: MediaQueryList;
    try {
      media = window.matchMedia(query);
    } catch {
      return;
    }
    const onChange = () => setReduced(media.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);
  return reduced;
}

/** Close a popover on outside pointer-down or Escape. */
export function useDismiss(open: boolean, ref: RefObject<HTMLElement | null>, onClose: () => void): void {
  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) onClose();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("pointerdown", onPointer, true);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("pointerdown", onPointer, true);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [open, ref, onClose]);
}
