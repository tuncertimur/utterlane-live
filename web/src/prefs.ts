// localStorage-backed preferences. Every access is guarded: storage can be
// unavailable (private mode, blocked site data) and the UI must still work.

const PREFIX = "utterlane.live.";

export type ThemePref = "system" | "dark" | "light";

function read(key: string): string | null {
  try {
    return globalThis.localStorage?.getItem(PREFIX + key) ?? null;
  } catch {
    return null;
  }
}

function write(key: string, value: string | null): void {
  try {
    if (value === null) globalThis.localStorage?.removeItem(PREFIX + key);
    else globalThis.localStorage?.setItem(PREFIX + key, value);
  } catch {
    // Ignore: preferences are a convenience.
  }
}

export const prefs = {
  micDeviceId(): string | null {
    return read("micDeviceId");
  },
  setMicDeviceId(value: string | null): void {
    write("micDeviceId", value);
  },
  theme(): ThemePref {
    const value = read("theme");
    return value === "dark" || value === "light" ? value : "system";
  },
  setTheme(value: ThemePref): void {
    write("theme", value === "system" ? null : value);
  },
  showSilent(): boolean {
    return read("showSilent") !== "0";
  },
  setShowSilent(value: boolean): void {
    write("showSilent", value ? null : "0");
  },
};
