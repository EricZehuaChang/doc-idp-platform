import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter, RouterView } from "vue-router";
import { afterEach, expect, it, vi } from "vitest";
import App from "../App.vue";
import { api, type ReviewDetail } from "../api";
import { i18n } from "../i18n";
import { clearSession, setSession } from "../session";
import ReviewView from "../views/ReviewView.vue";

afterEach(() => {
  clearSession();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("drops privileged task data when the tenant administrator signs out", async () => {
  setSession({ access_token: "test-session", email: "test@example.com", role: "tenant_admin", tenant_id: "a" });
  const client = new QueryClient();
  const router = createRouter({ history: createMemoryHistory(),
    routes: [{ path: "/login", component: { template: "<div />" } }] });
  await router.push("/login");
  const app = mount(App, { global: { plugins: [router, i18n, [VueQueryPlugin, { queryClient: client }]] } });
  try {
    client.setQueryData(["files", 1], ["coworker-task"]);
    client.setQueryData(["review", "coworker-task"], { name: "coworker-document" });
    clearSession();
    expect(client.getQueryData(["files", 1])).toBeUndefined();
    expect(client.getQueryData(["review", "coworker-task"])).toBeUndefined();
  } finally { app.unmount(); }
});

it("offers the original download but blocks review actions for a read-only pending task", async () => {
  const file: ReviewDetail = {
    file_id: "peer-task", file_name: "sample.pdf", status: "pending_verification", can_review: false,
    pages: [], result: {}, locked_by: null, verified_by: null, parent_file_id: null, page_count: 1,
    children: [], child_count: 0, pending_children: 0, status_counts: {}, active_file_id: "peer-task",
  };
  vi.spyOn(api, "detail").mockResolvedValue(file);
  vi.spyOn(api, "queue").mockResolvedValue([]);
  vi.spyOn(api, "fileArtifacts").mockResolvedValue({ artifacts: [] });
  const lock = vi.spyOn(api, "lock");
  const save = vi.spyOn(api, "downloadOriginal").mockResolvedValue();
  const router = createRouter({ history: createMemoryHistory(),
    routes: [{ path: "/review/:fileId", component: ReviewView, props: true }] });
  await router.push("/review/peer-task");
  const app = mount(RouterView, { global: { plugins: [router, i18n, VueQueryPlugin],
    stubs: { DocStage: true } } });
  try {
    await flushPromises();
    const buttons = app.findAll("button");
    expect(buttons.some(b => b.text() === i18n.global.t("review.start_validation_lock_l"))).toBe(false);
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "l" }));
    expect(lock).not.toHaveBeenCalled();
    expect(app.text()).toContain(i18n.global.t("review.read_only"));
    await buttons.find(b => b.text() === i18n.global.t("docStage.download_original"))!.trigger("click");
    expect(save).toHaveBeenCalledWith("peer-task", "sample.pdf");
  } finally { app.unmount(); }
});

it.each([undefined, "tenant-b"])("includes a tenant only when supplied on sign-in (%s)", async (tenant) => {
  const fetch = vi.fn().mockResolvedValue(new Response(null, { status: 401 }));
  vi.stubGlobal("fetch", fetch);
  await expect(api.login("member@example.com", "incorrect", tenant))
    .rejects.toThrow(i18n.global.t("login.check_credentials"));
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({
    email: "member@example.com", password: "incorrect", ...(tenant ? { tenant_id: tenant } : {}),
  });
});
