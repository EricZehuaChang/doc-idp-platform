// Adapted from KBase: a single catalog drives detection and the settings picker.
export type LanguageCode = "zh" | "en";
export interface Language {
  code: LanguageCode;
  nameKey: string;
}

export const LANGUAGES: Language[] = [
  { code: "zh", nameKey: "language.chinese" },
  { code: "en", nameKey: "language.english" },
];
export const DEFAULT_LANG: LanguageCode = "zh";
export const SUPPORTED_CODES: readonly string[] = LANGUAGES.map((l) => l.code);
