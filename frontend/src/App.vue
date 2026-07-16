<template>
  <!-- account entry pages render bare (no shell) -->
  <router-view v-if="bare" />

  <!-- admin-console shell: left sidebar + topbar (Vben/Soybean layout family) -->
  <div v-else class="shell">
    <aside class="side">
      <div class="logo">DOC·IDP</div>
      <nav>
        <router-link to="/queue"><span class="ico">☰</span> 待审队列</router-link>
        <router-link to="/skills"><span class="ico">⚙</span> 技能中心</router-link>
        <router-link to="/cabinet"><span class="ico">▤</span> 数据柜</router-link>
        <router-link to="/stats"><span class="ico">◔</span> 质量看板</router-link>
        <router-link v-if="isAdmin" to="/settings"><span class="ico">✦</span> 平台设置</router-link>
      </nav>
      <a class="help" href="#" @click.prevent="helpOpen = true"><span class="ico">?</span> 帮助</a>
    </aside>

    <div class="main">
      <header class="topbar">
        <span class="crumb">{{ pageTitle }}</span>
        <div class="user-menu" @click.stop="menuOpen = !menuOpen">
          <span class="avatar">{{ initial }}</span>
          <span class="uname">{{ displayName }}</span>
          <div v-if="menuOpen" class="dropdown" @click.stop>
            <template v-if="session.authRequired">
              <router-link to="/change-password" @click="menuOpen = false">修改密码</router-link>
              <a href="#" @click.prevent="logout">退出登录</a>
            </template>
            <span v-else class="dim">免登录模式（lite/dev）</span>
          </div>
        </div>
      </header>
      <router-view />
    </div>

    <HelpPanel v-if="helpOpen" @close="helpOpen = false" />
    <Toasts />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import HelpPanel from "./components/HelpPanel.vue";
import Toasts from "./components/Toasts.vue";
import { clearSession, session } from "./session";

const route = useRoute();
const router = useRouter();
const menuOpen = ref(false);
const helpOpen = ref(false);

const BARE = new Set(["/login", "/forgot", "/reset", "/activate"]);
const bare = computed(() => BARE.has(route.path));

const TITLES: Record<string, string> = {
  "/queue": "待人工校验", "/skills": "技能中心", "/cabinet": "数据柜",
  "/stats": "质量看板", "/settings": "平台设置", "/change-password": "修改密码",
};
const pageTitle = computed(() =>
  route.path.startsWith("/review/") ? "文档校验"
    : route.path.startsWith("/skills/") ? "技能编辑"
      : TITLES[route.path] ?? "");

const displayName = computed(() =>
  session.email || localStorage.getItem("idp_user") || "reviewer-1");
const initial = computed(() => (displayName.value[0] || "?").toUpperCase());
const isAdmin = computed(() => !session.authRequired || session.role === "admin");

function logout() {
  clearSession();
  menuOpen.value = false;
  router.push("/login");
}
function closeMenu() { menuOpen.value = false; }
onMounted(() => document.addEventListener("click", closeMenu));
onUnmounted(() => document.removeEventListener("click", closeMenu));
</script>

<style scoped>
.shell { display: flex; min-height: 100vh; }
.side { width: 190px; flex-shrink: 0; background: var(--bg-panel);
  border-right: 1px solid var(--border); display: flex; flex-direction: column;
  position: sticky; top: 0; height: 100vh; }
.logo { font-weight: 800; letter-spacing: 1px; color: var(--accent);
  padding: 16px; font-size: 16px; }
nav { display: flex; flex-direction: column; padding: 8px; gap: 2px; flex: 1; }
nav a, .help { display: flex; align-items: center; gap: 10px; padding: 9px 12px;
  border-radius: 8px; color: var(--text); font-size: 14px; }
nav a:hover, .help:hover { background: var(--bg-raised); }
nav a.router-link-active { background: var(--bg-raised); color: var(--accent);
  font-weight: 600; }
.ico { width: 16px; text-align: center; color: var(--text-dim); }
nav a.router-link-active .ico { color: var(--accent); }
.help { margin: 8px; color: var(--text-dim); }
.main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.topbar { display: flex; align-items: center; padding: 0 20px; height: 46px;
  background: var(--bg-panel); border-bottom: 1px solid var(--border);
  position: sticky; top: 0; z-index: 20; }
.crumb { font-weight: 600; }
.user-menu { margin-left: auto; display: flex; align-items: center; gap: 8px;
  cursor: pointer; position: relative; padding: 4px; }
.avatar { width: 26px; height: 26px; border-radius: 50%; background: var(--accent);
  color: var(--accent-text); display: inline-flex; align-items: center;
  justify-content: center; font-weight: 700; font-size: 13px; }
.uname { font-size: 13px; color: var(--text-dim); }
.dropdown { position: absolute; right: 0; top: 38px; background: var(--bg-raised);
  border: 1px solid var(--border); border-radius: 8px; min-width: 160px;
  display: flex; flex-direction: column; padding: 6px; z-index: 30; }
.dropdown a { padding: 8px 10px; border-radius: 6px; color: var(--text); font-size: 13px; }
.dropdown a:hover { background: var(--bg-panel); }
.dim { color: var(--text-dim); font-size: 12px; padding: 8px 10px; }

@media (max-width: 860px) {
  .side { width: 56px; }
  .logo { font-size: 11px; padding: 12px 6px; }
  nav a span:not(.ico), .help span:not(.ico) { display: none; }
}
</style>
