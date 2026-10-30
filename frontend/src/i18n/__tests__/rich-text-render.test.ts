// Real message compilation catches vue-i18n syntax errors that builds cannot.
// Adapted from KBase's feishu-i18n-t smoke test, covering every IDP rich key.
import { mount } from "@vue/test-utils";
import { createI18n, I18nT } from "vue-i18n";
import { defineComponent, h } from "vue";
import { describe, expect, it, vi } from "vitest";
import en from "../locales/en.json";
import zh from "../locales/zh.json";

const sources = import.meta.glob("../../**/*.vue", { eager: true, query: "?raw", import: "default" }) as Record<string, string>;
const richKeys = [...new Set(Object.values(sources).flatMap((source) =>
  [...source.matchAll(/<I18nT\b[^>]*\bkeypath="([^"]+)"/g)].map((match) => match[1])))];

function flatten(obj: Record<string, unknown>, prefix = ""): Record<string, string> {
  return Object.fromEntries(Object.entries(obj).flatMap(([name, value]) => {
    const key = prefix ? `${prefix}.${name}` : name;
    return typeof value === "object" && value !== null
      ? Object.entries(flatten(value as Record<string, unknown>, key)) : [[key, String(value)]];
  }));
}
const flat = { zh: flatten(zh), en: flatten(en) };

function renderMessage(locale: "zh" | "en", keypath: string) {
  const message = flat[locale][keypath];
  expect(message, keypath).toBeDefined();
  const names = [...new Set([...message.matchAll(/\{([a-zA-Z_][\w]*)\}/g)].map((m) => m[1]))];
  const instance = createI18n({ legacy: false, locale, fallbackLocale: "zh", messages: { zh, en } });
  const component = defineComponent({ setup: () => () => h(
    I18nT as unknown as ReturnType<typeof defineComponent>,
    { keypath, tag: "p", scope: "global" },
    Object.fromEntries(names.map((name) => [name, () => h("b", { "data-slot": name }, `SLOT_${name}_END`)])),
  ) });
  const wrapper = mount(component, { global: { plugins: [instance] } });
  const text = wrapper.text();
  expect(text, keypath).not.toBe(keypath);
  for (const name of names) {
    expect(wrapper.find(`[data-slot="${name}"]`).exists(), `${keypath}: ${name}`).toBe(true);
    expect(text).toContain(`SLOT_${name}_END`);
    expect(text).not.toContain(`{${name}}`);
  }
  wrapper.unmount();
  return text;
}

describe("mounted I18nT rich messages", () => {
  it("discovers the actual component rich keys", () => {
    expect(richKeys.length).toBeGreaterThan(0);
  });
  it("matches the named slots actually declared by components", () => {
    for (const [file, source] of Object.entries(sources)) {
      for (const match of source.matchAll(/<I18nT\b([^>]*\bkeypath="([^"]+)"[^>]*)>([\s\S]*?)<\/I18nT>/g)) {
        const key = match[2];
        const slots = [...new Set([...match[3].matchAll(/<template\s+#([\w]+)/g)].map((m) => m[1]))].sort();
        const parameters = [...new Set([...flat.zh[key].matchAll(/\{([a-zA-Z_][\w]*)\}/g)].map((m) => m[1]))].sort();
        expect(slots, `${file}: ${key}`).toEqual(parameters);
        expect(match[1], `${file}: ${key}`).toContain('scope="global"');
        expect(source, file).toMatch(/import\s*\{[^}]*\bI18nT\b[^}]*\}\s*from\s*["']vue-i18n["']/);
      }
    }
  });
  it.each(["zh", "en"] as const)("renders all rich keys and named slots [%s]", (locale) => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      for (const key of richKeys) renderMessage(locale, key);
      expect(errors.mock.calls).toEqual([]);
    } finally { errors.mockRestore(); }
  });
  it.each(["zh", "en"] as const)("compiles every message, including escaped @ and | [%s]", (locale) => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      for (const [key, message] of Object.entries(flat[locale])) {
        const text = renderMessage(locale, key);
        if (message.includes("{'@'}")) expect(text, key).toContain("@");
        if (message.includes("{'|'}")) expect(text, key).toContain("|");
      }
      expect(errors.mock.calls).toEqual([]);
    } finally { errors.mockRestore(); }
  });
});
