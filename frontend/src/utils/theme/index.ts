/**
 * Light / dark theme.
 *
 * The OS preference is the default and tokens.css honours it with no script at
 * all. This only records an explicit choice, as `data-theme` on <html>, so a
 * receptionist on a bright monitor and a doctor on a night shift can each pick.
 *
 * Remembering the choice is a convenience: storage can be blocked (private
 * windows, locked-down clinic PCs) and every access is guarded so that losing
 * it never breaks rendering.
 */

export type Theme = 'light' | 'dark';

const STORAGE_KEY = 'healthguard.theme';

export function storedTheme(): Theme | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value === 'light' || value === 'dark' ? value : null;
  } catch {
    return null;
  }
}

export function currentTheme(): Theme {
  const explicit = document.documentElement.dataset.theme;
  if (explicit === 'light' || explicit === 'dark') return explicit;
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // Not remembered; still applied for this visit.
  }
}

/** Called once before the first render, so a stored choice never flashes. */
export function restoreTheme(): void {
  const theme = storedTheme();
  if (theme) document.documentElement.dataset.theme = theme;
}
