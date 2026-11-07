import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter, RouterView } from "vue-router";
import { afterEach, expect, it, vi } from "vitest";
import { api, type ReviewDetail } from "../api";
import { i18n } from "../i18n";
import ReviewView from "../views/ReviewView.vue";

afterEach(() => { vi.restoreAllMocks(); sessionStorage.clear(); });

it("binds fields, item edits, revisions and record actions to the selected record", async () => {
  const rows = ["Left", "Right"].map(name => ({
    store: { $value: name, $confidence: 3, $bbox: [], $pages: 1 },
    items: [{ dish: name + " item" }],
  }));
  let revision = 3;
  const detail = (): ReviewDetail => ({
    file_id: "receipts", file_name: "receipts.png", status: "pending_verification", can_review: true,
    pages: [], result: { records: structuredClone(rows) }, output_shape: "list", result_revision: revision,
    review_reasons: ["records_overlap"], table_columns: { items: ["dish"] },
    locked_by: null, verified_by: null, parent_file_id: null, page_count: 1,
    children: [], child_count: 0, pending_children: 0, status_counts: {}, active_file_id: "receipts",
  });
  vi.spyOn(api, "detail").mockImplementation(async () => detail());
  vi.spyOn(api, "queue").mockResolvedValue([]);
  vi.spyOn(api, "fileArtifacts").mockResolvedValue({ artifacts: [] });
  vi.spyOn(api, "lock").mockResolvedValue({});
  const patch = vi.spyOn(api, "patchFields").mockImplementation(async (_, edits, version) => {
    expect(version).toBe(revision);
    for (const edit of edits) {
      expect(edit.record).toBe(1);
      if (edit.field === "store") rows[1].store.$value = edit.value!;
      if (edit.field === "items") rows[1].items = JSON.parse(JSON.stringify(edit.rows));
    }
    revision++;
  });
  const add = vi.spyOn(api, "addRecord").mockImplementation(async (_, version) => {
    expect(version).toBe(revision);
    rows.push({ store: { $value: "", $confidence: 0, $bbox: [], $pages: 1 }, items: [] });
    revision++;
    return { record_index: rows.length - 1 };
  });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  const remove = vi.spyOn(api, "deleteRecord").mockImplementation(async (_, index, version) => {
    expect(version).toBe(revision);
    rows.splice(index, 1);
    revision++;
  });
  const client = new QueryClient();
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: "/review/:fileId", component: ReviewView, props: true },
  ] });
  await router.push("/review/receipts");
  const app = mount(RouterView, { global: { plugins: [router, i18n, [VueQueryPlugin, { queryClient: client }]],
    stubs: { DocStage: true } } });
  const button = (key: string) => app.findAll("button").find(b => b.text().includes(i18n.global.t(key)))!;
  try {
    await flushPromises();
    expect(app.text()).toContain(i18n.global.t("review.records_overlap"));
    expect(app.get("textarea").element.value).toBe("Left");
    await button("review.start_validation_lock_l").trigger("click");
    await flushPromises();
    await app.get("[data-record-nav] select").setValue("1");
    await flushPromises();
    expect(app.get("textarea").element.value).toBe("Right");
    await app.get("textarea").setValue("Edited right");
    await app.get(".cell-input").setValue("Second dish");
    await button("review.save_corrections_s").trigger("click");
    await flushPromises();
    expect(patch).toHaveBeenCalledTimes(1);
    expect(rows[1].items).toEqual([{ dish: "Second dish" }]);
    expect(rows[0].store.$value).toBe("Left");

    await button("review.add_record").trigger("click");
    await flushPromises();
    expect(add).toHaveBeenCalledOnce();
    expect(app.get("textarea").element.value).toBe("");
    await button("review.row_bc9735").trigger("click");
    expect(app.find(".cell-input").exists()).toBe(true);
    await button("review.delete_record").trigger("click");
    await flushPromises();
    expect(remove).toHaveBeenCalledWith("receipts", 2, 5);
    expect(app.get("textarea").element.value).toBe("Edited right");
  } finally { app.unmount(); client.clear(); }
});
