// Global toast store (UX debt: errors were bare text in random corners).
// toast.error/ok push a message; Toasts.vue renders and auto-expires them.
import { reactive } from "vue";

export interface ToastItem { id: number; kind: "ok" | "error"; text: string }

let seq = 0;
export const toasts = reactive<ToastItem[]>([]);

function push(kind: "ok" | "error", text: string, ttlMs: number) {
  const id = ++seq;
  toasts.push({ id, kind, text });
  setTimeout(() => {
    const i = toasts.findIndex((t) => t.id === id);
    if (i >= 0) toasts.splice(i, 1);
  }, ttlMs);
}

export const toast = {
  ok: (text: string) => push("ok", text, 2500),
  error: (e: unknown) =>
    push("error", e instanceof Error ? e.message : String(e), 5000),
};
