/**
 * Internationalisation (AUDIT 7.3).
 *
 * "Multilingual from day one — it is the documented pain point and a retrofit
 * is painful." 61% of Indian practices report multilingual documentation
 * friction, so English, Hindi and Marathi ship together rather than English
 * first and the others later.
 *
 * A locale file with a missing key falls back to English rather than rendering
 * a raw key, and there is a test asserting all three files have the same keys -
 * a half-translated page is worse than an untranslated one when the subject is
 * someone's symptoms.
 */

import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

import en from './locales/en.json';
import hi from './locales/hi.json';
import mr from './locales/mr.json';

export const SUPPORTED_LANGUAGES = [
  { code: 'en', label: 'English' },
  { code: 'hi', label: 'हिन्दी' },
  { code: 'mr', label: 'मराठी' },
] as const;

export type LanguageCode = (typeof SUPPORTED_LANGUAGES)[number]['code'];

const STORAGE_KEY = 'healthguard.language';

function initialLanguage(): LanguageCode {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored && SUPPORTED_LANGUAGES.some((l) => l.code === stored)) {
      return stored as LanguageCode;
    }
  } catch {
    // Private browsing, or site data blocked. Fall through to the browser's
    // preference rather than failing to render.
  }

  const browser = navigator.language?.split('-')[0];
  return SUPPORTED_LANGUAGES.some((l) => l.code === browser)
    ? (browser as LanguageCode)
    : 'en';
}

void i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
    hi: { translation: hi },
    mr: { translation: mr },
  },
  lng: initialLanguage(),
  fallbackLng: 'en',
  interpolation: { escapeValue: false },
  // A missing key is a bug worth seeing in development, not one to paper over.
  saveMissing: import.meta.env.DEV,
  missingKeyHandler: import.meta.env.DEV
    ? (_lngs, _ns, key) => console.warn(`[i18n] missing key: ${key}`)
    : undefined,
});

export function setLanguage(code: LanguageCode): void {
  void i18n.changeLanguage(code);
  document.documentElement.lang = code;
  try {
    localStorage.setItem(STORAGE_KEY, code);
  } catch {
    // Remembering the choice is a convenience; losing it must not break the app.
  }
}

document.documentElement.lang = i18n.language;

export default i18n;
