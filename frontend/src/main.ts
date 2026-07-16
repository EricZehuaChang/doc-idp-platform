import { VueQueryPlugin } from "@tanstack/vue-query";
import { createApp } from "vue";
import { createRouter, createWebHashHistory } from "vue-router";
import App from "./App.vue";
import { probeAuth, session } from "./session";
import "./style.css";
import ChangePasswordView from "./views/auth/ChangePasswordView.vue";
import ForgotView from "./views/auth/ForgotView.vue";
import LoginView from "./views/auth/LoginView.vue";
import OidcCallbackView from "./views/auth/OidcCallbackView.vue";
import TokenPasswordView from "./views/auth/TokenPasswordView.vue";
import CabinetView from "./views/CabinetView.vue";
import DashboardView from "./views/DashboardView.vue";
import HomeView from "./views/HomeView.vue";
import QueueView from "./views/QueueView.vue";
import ReviewView from "./views/ReviewView.vue";
import SettingsView from "./views/SettingsView.vue";
import SkillEditorView from "./views/SkillEditorView.vue";
import SkillsView from "./views/SkillsView.vue";

// entry pages reachable without a session (account flows carry their own token)
const PUBLIC = new Set(["/login", "/forgot", "/reset", "/activate", "/oidc"]);

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", redirect: "/home" },
    { path: "/home", component: HomeView },
    { path: "/login", component: LoginView },
    { path: "/forgot", component: ForgotView },
    { path: "/reset", component: TokenPasswordView, props: { mode: "reset" } },
    { path: "/activate", component: TokenPasswordView, props: { mode: "activate" } },
    { path: "/oidc", component: OidcCallbackView },
    { path: "/change-password", component: ChangePasswordView },
    { path: "/tasks", component: QueueView },
    { path: "/queue", redirect: "/tasks" },
    { path: "/review/:fileId", component: ReviewView, props: true },
    { path: "/skills", component: SkillsView },
    { path: "/skills/:code", component: SkillEditorView, props: true },
    { path: "/cabinet", component: CabinetView },
    { path: "/dashboard", component: DashboardView },
    { path: "/stats", redirect: "/dashboard" },
    { path: "/settings", component: SettingsView },
  ],
});

router.beforeEach(async (to) => {
  if (PUBLIC.has(to.path)) return true;
  if (session.authRequired === null) await probeAuth();   // boot probe, once
  if (session.authRequired && !session.token) return "/login";
  // forced first-login password change blocks everything else
  if (session.mustChangePassword && to.path !== "/change-password") return "/change-password";
  return true;
});

// TanStack Query (caching design §9.0 layer ①): server state cached across
// route hops; stale-while-revalidate keeps weak networks usable.
createApp(App)
  .use(router)
  .use(VueQueryPlugin, {
    queryClientConfig: {
      defaultOptions: {
        queries: { staleTime: 5_000, retry: 1, refetchOnWindowFocus: false },
      },
    },
  })
  .mount("#app");
