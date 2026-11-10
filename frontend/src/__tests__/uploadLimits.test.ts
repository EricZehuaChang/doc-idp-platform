import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../api";
import { i18n } from "../i18n";
import UploadView from "../views/UploadView.vue";

const MiB = 1024 * 1024;

function sizedFile(name: string, bytes: number): File {
  const file = new File(["a"], name, { type: "image/png" });
  // Exercise the browser's File.size contract without allocating hundreds of MiB in CI.
  Object.defineProperty(file, "size", { value: bytes });
  return file;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("allows a 200 MiB studio sample and blocks an extra byte before transmission", async () => {
  const request = vi.fn().mockResolvedValue(Response.json({ id: "sample" }));
  vi.stubGlobal("fetch", request);
  await expect(api.studioUploadSample(sizedFile("scan.pdf", 200 * MiB))).resolves.toMatchObject({ id: "sample" });
  await expect(api.studioUploadSample(sizedFile("large.pdf", 200 * MiB + 1))).rejects.toThrow("200 MiB");
  expect(request).toHaveBeenCalledTimes(1);
});

it("uses the fallback size limits for selection and batching until formats loads", async () => {
  vi.spyOn(api, "formats").mockRejectedValue(new Error("formats unavailable"));
  vi.spyOn(api, "skills").mockResolvedValue([
    { skill_code: "scan", kind: "layout", name: "Scan", published_version: 1, state: "active" },
  ]);
  vi.spyOn(api, "homeStats").mockRejectedValue(new Error("unavailable"));
  const submit = vi.spyOn(api, "submitProcess").mockResolvedValue({ transaction_id: "test", files: [] });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/upload", component: UploadView }] });
  await router.push("/upload");
  const view = mount(UploadView, { global: { plugins: [router, i18n, [VueQueryPlugin, { queryClient: client }]],
    stubs: { RouterLink: true } } });
  try {
    await flushPromises();
    const input = view.get('input[type="file"]');
    Object.defineProperty(input.element, "files", { value: [
      sizedFile("first.png", 200 * MiB), sizedFile("second.png", 40 * MiB),
      sizedFile("third.png", 1), sizedFile("oversized.png", 200 * MiB + 1),
    ] });
    await input.trigger("change");
    expect(view.get(".reason").text()).toContain("200");
    await view.get(".submit-bar button.primary").trigger("click");
    await flushPromises();
    expect(submit.mock.calls.map(([files]) => files.map(file => file.name))).toEqual([
      ["first.png", "second.png"], ["third.png"],
    ]);
  } finally {
    view.unmount();
    client.clear();
  }
});
