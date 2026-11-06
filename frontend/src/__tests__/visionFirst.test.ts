import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter, RouterView } from "vue-router";
import { afterEach, expect, it, vi } from "vitest";
import { api, type SkillPackage } from "../api";
import { i18n } from "../i18n";
import SkillEditorView from "../views/SkillEditorView.vue";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("opts in from a legacy draft, reloads the saved value and clears it in incompatible modes", async () => {
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  let saved: SkillPackage = {
    skill_code: "invoice", name: "Invoice", description: "", kind: "extract", doc_type_hint: "",
    system_prompt: "", few_shot: [], validators: [], additional_rules: "", parser: null,
    fields: [{ name: "amount", type: "string", instruction: "", mode: "verbatim", required: false,
      anchor_hints: [], enum_values: [], columns: [] }],
    model_binding: { extractor: "", fallback: null, challenger: null },
    review_policy: { mode: "auto", confidence_threshold: 2 },
  };
  const load = vi.spyOn(api, "skillDetail").mockImplementation(async () => ({
    skill_code: "invoice", name: "Invoice", kind: "extract", state: "draft", selected_version: 1,
    latest_package: structuredClone(saved), versions: [{ version: 1, status: "draft", changelog: "" }],
  }));
  const save = vi.spyOn(api, "skillSaveDraft").mockImplementation(async (_code, version, pkg) => {
    saved = structuredClone(pkg);
    return { version, status: "draft" };
  });
  vi.spyOn(api, "skillOptions").mockResolvedValue({ providers: [], fallback_chain: [], parsers: [] });
  vi.spyOn(api, "referenceSkills").mockResolvedValue({ skills: [] });
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: "/skills", component: { template: "<div />" } },
    { path: "/skills/:code", component: SkillEditorView, props: true },
  ] });
  await router.push("/skills/invoice");
  const app = mount(RouterView, { global: { plugins: [router, i18n], stubs: { SamplePanel: true } } });
  const vision = () => app.get<HTMLInputElement>(".vision-first input");
  const choose = async (key: string) => {
    await app.findAll("button.mode-card").find(b => b.text().includes(i18n.global.t(key)))!.trigger("click");
  };
  try {
    await flushPromises();
    expect(vision().element.checked).toBe(false);
    await vision().setValue(true);
    await app.get(".acts > button.primary").trigger("click");
    await flushPromises();
    expect(saved.vision_first).toBe(true);
    expect(load).toHaveBeenCalledTimes(2);
    expect(vision().element.checked).toBe(true);

    await choose("skillEditor.rules_first");
    expect(app.find(".vision-first").exists()).toBe(false);
    await choose("skillEditor.model_extraction");
    expect(vision().element.checked).toBe(false);
    await vision().setValue(true);
    await app.get("[data-testid=fast-mode-card]").trigger("click");
    expect(app.find(".vision-first").exists()).toBe(false);
    await choose("skillEditor.balanced");
    expect(vision().element.checked).toBe(false);
    await app.get(".acts > button.primary").trigger("click");
    await flushPromises();
    expect(save).toHaveBeenCalledTimes(2);
    expect(saved.vision_first).toBe(false);
  } finally { app.unmount(); }
});
