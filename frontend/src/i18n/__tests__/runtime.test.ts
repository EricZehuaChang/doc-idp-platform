import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { computed, defineComponent, h, nextTick } from "vue";
import { mount } from "@vue/test-utils";
import { detectLanguage, i18n, initI18n, setLanguage, STORAGE_KEY, translateMessage } from "../index";
import { STATUS_LABELS, RUN_STATUS_LABELS, VERSION_LABELS, initiatorLabel } from "../../labels";
import { buildResultJson, buildResultMarkdown, buildResultMarkdownLines, type ResultExportInput } from "../../resultExport";
import { toast, toasts } from "../../toast";
import zh from "../locales/zh.json";
import en from "../locales/en.json";

beforeEach(() => { localStorage.clear(); setLanguage("zh"); localStorage.clear(); });
afterEach(() => { vi.restoreAllMocks(); toasts.splice(0); });

describe("language preference", () => {
  it("prefers storage over browser language", () => {
    vi.spyOn(navigator, "language", "get").mockReturnValue("en-US");
    localStorage.setItem(STORAGE_KEY, "zh");
    expect(detectLanguage()).toBe("zh");
  });
  it("uses browser language when storage is invalid or absent", () => {
    vi.spyOn(navigator, "language", "get").mockReturnValue("EN-gb");
    localStorage.setItem(STORAGE_KEY, "fr");
    expect(detectLanguage()).toBe("en");
  });
  it("falls back to zh for unsupported browser languages", () => {
    vi.spyOn(navigator, "language", "get").mockReturnValue("fr-FR");
    expect(detectLanguage()).toBe("zh");
  });
  it("survives disabled storage and still switches html lang", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new DOMException(); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException(); });
    vi.spyOn(navigator, "language", "get").mockReturnValue("en-US");
    expect(detectLanguage()).toBe("en");
    setLanguage("en");
    expect(document.documentElement.lang).toBe("en");
  });
  it("persists switches and initializes html lang", () => {
    setLanguage("en");
    expect(i18n.global.locale.value).toBe("en");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("en");
    expect(document.documentElement.lang).toBe("en");
    document.documentElement.lang = "";
    initI18n();
    expect(document.documentElement.lang).toBe("en");
    setLanguage("fr");
    expect(i18n.global.locale.value).toBe("en");
  });
});

describe("render-time display text", () => {
  it("updates status labels in the same mounted component", async () => {
    const component = defineComponent({ setup() {
      const labels = computed(STATUS_LABELS);
      return () => h("p", `${labels.value.queued} / ${RUN_STATUS_LABELS().running} / ${VERSION_LABELS().draft}`);
    } });
    const wrapper = mount(component, { global: { plugins: [i18n] } });
    expect(wrapper.text()).toBe(`${zh.labels.queued} / ${zh.labels.running} / ${zh.labels.draft}`);
    setLanguage("en"); await nextTick();
    expect(wrapper.text()).toBe(`${en.labels.queued} / ${en.labels.running} / ${en.labels.draft}`);
    wrapper.unmount();
  });
  it("keeps pure actor helpers language-independent", () => {
    expect(initiatorLabel("anonymous")).toEqual({ key: "labels.anonymous" });
    expect(initiatorLabel(null)).toEqual({ key: "labels.anonymous" });
    expect(initiatorLabel("user@example.test")).toBe("user@example.test");
    const message = initiatorLabel("anonymous");
    expect(translateMessage(message)).toBe(zh.labels.anonymous);
    setLanguage("en");
    expect(translateMessage(message)).toBe(en.labels.anonymous);
  });
  it("resolves message descriptors when toast text is read", () => {
    vi.useFakeTimers();
    toast.ok({ key: "labels.completed" });
    expect(toasts[0].text).toBe(zh.labels.completed);
    setLanguage("en");
    expect(toasts[0].text).toBe(en.labels.completed);
    vi.runAllTimers(); vi.useRealTimers();
  });
});

const input: ResultExportInput = {
  fileId: "test-file", fileName: "test.pdf", status: "completed", statusLabel: "Completed",
  pageCount: 2, verifiedBy: null,
  fields: [{ name: "Amount", value: "3|4", confidence: 3 }],
  tables: [{ name: "Items", rows: [{ Item: "Pen", $confidence: "3" }] }],
};
const at = new Date("2026-10-30T00:00:00Z");

describe("result export localization", () => {
  it("pure builder returns descriptors instead of capturing a locale", () => {
    const lines = buildResultMarkdownLines(input, at);
    expect(lines[0]).toEqual({ key: "result_export.heading", params: { name: "test.pdf" } });
    setLanguage("en");
    expect(buildResultMarkdownLines(input, at)).toEqual(lines);
  });
  it("export headers follow the current language without remounting", () => {
    expect(buildResultMarkdown(input, at)).toContain(i18n.global.t("result_export.fields"));
    const before = buildResultMarkdown(input, at);
    setLanguage("en");
    const after = buildResultMarkdown(input, at);
    expect(after).not.toBe(before);
    expect(after).toContain("| Field | Value | Confidence |");
    expect(after).toContain("| Amount | 3\\|4 | 3 |");
    expect(after).not.toContain("$confidence");
    expect(after).not.toContain("result_export.");
    expect(after).not.toMatch(/[\u3400-\u9fff]/u);
  });
  it("preserves machine-readable JSON keys and strips metadata", () => {
    const before = buildResultJson(input, at);
    setLanguage("en");
    expect(buildResultJson(input, at)).toEqual(before);
    expect(before.result).toEqual({ Amount: "3|4", Items: [{ Item: "Pen" }] });
  });
});
