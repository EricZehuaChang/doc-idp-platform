// Adapted from KBase. IDP keeps language preferences on this browser only.
import { createI18n } from "vue-i18n";
import en from "./locales/en.json";
import zh from "./locales/zh.json";
import { DEFAULT_LANG, SUPPORTED_CODES, type LanguageCode } from "./languages";

export const STORAGE_KEY = "idp-lang";

export function detectLanguage(): LanguageCode {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved && SUPPORTED_CODES.includes(saved)) return saved as LanguageCode;
  } catch {
    // Storage may be unavailable in a restricted browser. Detection still works.
  }
  const prefix = (navigator.language || "").slice(0, 2).toLowerCase();
  return SUPPORTED_CODES.includes(prefix) ? prefix as LanguageCode : DEFAULT_LANG;
}

export const i18n = createI18n({
  legacy: false,
  globalInjection: true,
  locale: detectLanguage(),
  fallbackLocale: DEFAULT_LANG,
  messages: { zh, en },
});

export function setLanguage(lang: string): void {
  if (!SUPPORTED_CODES.includes(lang)) return;
  i18n.global.locale.value = lang as LanguageCode;
  document.documentElement.setAttribute("lang", lang);
  try { localStorage.setItem(STORAGE_KEY, lang); } catch {
    // Keep the current session switch usable even when persistence is blocked.
  }
}

export function initI18n(): void {
  document.documentElement.setAttribute("lang", i18n.global.locale.value);
}

// Pure display helpers return descriptors; UI boundaries translate them.
export interface MessageDescriptor {
  key: string;
  params?: Record<string, string | number>;
}
export type DisplayMessage = string | MessageDescriptor;

export function translateMessage(message: DisplayMessage): string {
  return typeof message === "string" ? message
    : i18n.global.t(message.key, message.params ?? {});
}
