import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter, RouterView } from "vue-router";
import { afterEach, expect, it, vi } from "vitest";
import { api, type FieldSpec, type ReviewDetail, type TableHeaderInfo } from "../api";
import FieldCard from "../components/FieldCard.vue";
import { i18n } from "../i18n";
import ReviewView from "../views/ReviewView.vue";

afterEach(() => vi.restoreAllMocks());

it("opts in on an unspecified table and clears the option after adding an explicit column", async () => {
  const field: FieldSpec = {
    name: "items", type: "table", instruction: "", mode: "verbatim", required: false,
    columns: [], enum_values: [], anchor_hints: [],
  };
  const app = mount(FieldCard, { props: { field, inline: true, expanded: true },
    global: { plugins: [i18n], stubs: { FieldEditor: true } } });
  try {
    const checkbox = app.get<HTMLInputElement>(".infer-headers input");
    expect(checkbox.element.checked).toBe(false);
    await checkbox.setValue(true);
    const enabled = app.emitted<[FieldSpec]>("update")![0][0];
    expect(enabled.infer_headers).toBe(true);
    await app.setProps({ field: enabled });
    await app.get(".add-child").trigger("click");
    const defined = app.emitted<[FieldSpec]>("update")![1][0];
    expect(defined.columns).toHaveLength(1);
    expect(defined.infer_headers).toBeUndefined();
    await app.setProps({ field: defined });
    expect(app.find(".infer-headers").exists()).toBe(false);
    await app.setProps({ field: { ...field, entity_list: true } });
    expect(app.find(".infer-headers").exists()).toBe(false);
  } finally { app.unmount(); }
});

it.each(["object", "list"] as const)("uses the ordered header evidence for %s results", async shape => {
  const record = { items: [{ Fee: "12", "列2": "23" }] };
  const info: TableHeaderInfo = { columns: ["列2", "Fee"], sources: { "列2": "positional", Fee: "inferred" } };
  const fields: ReviewDetail = {
    file_id: "headers", file_name: "grid.pdf", status: "completed", can_review: false,
    pages: [], result: shape === "list" ? { records: [record, record] } : record, output_shape: shape,
    review_reasons: ["table_headers_inferred"], table_headers: shape === "list"
      ? { "records[0].items": info, "records[1].items": { columns: ["Fee", "列2"], sources: { Fee: "original", "列2": "positional" } } }
      : { items: info },
    locked_by: null, verified_by: null, parent_file_id: null, page_count: 1,
    children: [], child_count: 0, pending_children: 0, status_counts: {}, active_file_id: "headers",
  };
  vi.spyOn(api, "detail").mockResolvedValue(fields);
  vi.spyOn(api, "queue").mockResolvedValue([]);
  vi.spyOn(api, "fileArtifacts").mockResolvedValue({ artifacts: [] });
  const client = new QueryClient();
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: "/review/:fileId", component: ReviewView, props: true },
  ] });
  await router.push("/review/headers");
  const app = mount(RouterView, { global: { plugins: [router, i18n, [VueQueryPlugin, { queryClient: client }]],
    stubs: { DocStage: true } } });
  try {
    await flushPromises();
    const positional = i18n.global.t("review.positional_column", { n: 2 });
    expect(app.findAll("th")[0].text()).toBe(positional);
    expect(app.get("th .badge-inferred").text()).toBe(i18n.global.t("review.inferred_header"));
    expect(app.findAll("tbody td")[0].text()).toBe("23");
    expect(app.text()).toContain(i18n.global.t("review.table_headers_inferred"));
    if (shape === "list") {
      await app.get("[data-record-nav] select").setValue("1");
      await flushPromises();
      expect(app.findAll("th").map(th => th.text())).toEqual(["Fee", positional]);
      expect(app.find("th .badge-inferred").exists()).toBe(false);
    }
  } finally { app.unmount(); client.clear(); }
});
