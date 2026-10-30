import { i18n, type DisplayMessage } from "./i18n";

// Resolve at render time so status chips and filter choices follow the locale.
const STATUS_KEYS: Record<string, string> = {
  queued: "labels.queued", processing: "labels.processing",
  pending_verification: "labels.pending_verification", completed: "labels.completed",
  passed: "labels.passed", rejected: "labels.rejected", split: "labels.split",
  error: "labels.failed",
};
const RUN_STATUS_KEYS: Record<string, string> = {
  queued: "labels.queued", running: "labels.running", completed: "labels.done",
  needs_review: "labels.needs_review", failed: "labels.failed",
};
const VERSION_KEYS: Record<string, string> = {
  draft: "labels.draft", published: "labels.published", archived: "labels.archived",
};
const translatedLabels = (keys: Record<string, string>): Record<string, string> =>
  Object.fromEntries(Object.entries(keys).map(([value, key]) => [value, i18n.global.t(key)]));

export const STATUS_LABELS = () => translatedLabels(STATUS_KEYS);
/** 走查 #19: the Playground run vocabulary (server-derived, D2) */
export const RUN_STATUS_LABELS = () => translatedLabels(RUN_STATUS_KEYS);
export const VERSION_LABELS = () => translatedLabels(VERSION_KEYS);

/** D5 (#19): the no-login actor is stored as "anonymous" — the UI says 免登录 */
export function initiatorLabel(name: string | null | undefined): DisplayMessage {
  return !name || name === "anonymous" ? { key: "labels.anonymous" } : name;
}
