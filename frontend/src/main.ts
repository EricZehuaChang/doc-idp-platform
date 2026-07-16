import { VueQueryPlugin } from "@tanstack/vue-query";
import { createApp } from "vue";
import { createRouter, createWebHashHistory } from "vue-router";
import App from "./App.vue";
import { probeAuth, session } from "./session";
import "./style.css";
import ChangePasswordView from "./views/auth/ChangePasswordView.vue";
import ForgotView from "./views/auth/ForgotView.vue";
import LoginView from "./views/auth/LoginView.vue";
import TokenPasswordView from "./views/auth/TokenPasswordView.vue";
import CabinetView from "./views/CabinetView.vue";
import QueueView from "./views/QueueView.vue";
import ReviewView from "./views/ReviewView.vue";
import SettingsView from "./views/SettingsView.vue";
import SkillsView from "./views/SkillsView.vue";
import StatsView from "./views/StatsView.vue";

// entry pages reachable without a session (account flows carry their own token)
const PUBLIC = new Set(["/login", "/forgot", "/reset", "/activate"]);

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", redirect: "/queue" },
    { path: "/login", component: LoginView },
    { path: "/forgot", component: ForgotView },
    { path: "/reset", component: TokenPasswordView, props: { mode: "reset" } },
    { path: "/activate", component: TokenPasswordView, props: { mode: "activate" } },
    { path: "/change-password", component: ChangePasswordView },
    { path: "/queue", component: QueueView },
    { path: "/review/:fileId", component: ReviewView, props: true },
    { path: "/skills", component: SkillsView },
    { path: "/cabinet", component: CabinetView },
    { path: "/stats", component: StatsView },
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
