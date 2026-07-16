import { createApp } from "vue";
import { createRouter, createWebHashHistory } from "vue-router";
import App from "./App.vue";
import "./style.css";
import CabinetView from "./views/CabinetView.vue";
import QueueView from "./views/QueueView.vue";
import ReviewView from "./views/ReviewView.vue";
import StatsView from "./views/StatsView.vue";

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", redirect: "/queue" },
    { path: "/queue", component: QueueView },
    { path: "/review/:fileId", component: ReviewView, props: true },
    { path: "/cabinet", component: CabinetView },
    { path: "/stats", component: StatsView },
  ],
});

createApp(App).use(router).mount("#app");
