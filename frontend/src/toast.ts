// Global toast store (UX debt: errors were bare text in random corners).
// toast.error/ok push a message; Toasts.vue renders and auto-expires them.
import { reactive } from "vue";
import { translateMessage, type DisplayMessage } from "./i18n";

export interface ToastItem { id: number; kind: "ok" | "error"; readonly text: string }

let seq = 0;
export const toasts = reactive<ToastItem[]>([]);

function push(kind: "ok" | "error", message: DisplayMessage, ttlMs: number) {
  const id = ++seq;
  toasts.push({ id, kind, get text() { return translateMessage(message); } });
  setTimeout(() => {
    const i = toasts.findIndex((t) => t.id === id);
    if (i >= 0) toasts.splice(i, 1);
  }, ttlMs);
}

export const toast = {
  ok: (text: DisplayMessage) => push("ok", text, 2500),
  error: (e: unknown) =>
    push("error", e instanceof Error ? e.message : isMessage(e) ? e : String(e), 5000),
};

function isMessage(value: unknown): value is DisplayMessage {
  return typeof value === "object" && value !== null && "key" in value && typeof value.key === "string";
}
