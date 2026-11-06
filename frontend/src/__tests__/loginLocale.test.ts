import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { expect, it, vi } from "vitest";
import { i18n, setLanguage } from "../i18n";
import LoginView from "../views/auth/LoginView.vue";

it("retranslates a visible credential error when switching the login language", async () => {
  const originalLocale = i18n.global.locale.value;
  setLanguage("zh");
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: "/login", component: LoginView }, { path: "/forgot", redirect: "/login" }],
  });
  await router.push("/login");
  vi.stubGlobal("fetch", vi.fn(async (url: string) => url.endsWith("/login")
    ? new Response(null, { status: 401 }) : Response.json({ enabled: false })));
  const view = mount(LoginView, { global: { plugins: [router, i18n] } });
  try {
    await view.get("form").trigger("submit");
    await flushPromises();
    expect(view.get('[role="alert"]').text()).toBe(i18n.global.t("login.check_credentials"));
    await view.get('button[lang="en"]').trigger("click");
    expect(i18n.global.locale.value).toBe("en");
    expect(view.get('[role="alert"]').text()).toBe(i18n.global.t("login.check_credentials"));
  } finally {
    view.unmount();
    vi.unstubAllGlobals();
    setLanguage(originalLocale);
  }
});
