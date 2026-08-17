<template>
  <!-- account entry pages render bare (no shell) -->
  <router-view v-if="bare" />

  <!-- Insavlo-style shell: top navbar, content below -->
  <div v-else class="shell">
    <header class="navbar">
      <span class="logo">DOC·IDP</span>
      <nav>
        <router-link to="/home">主页</router-link>
        <router-link to="/tasks">任务</router-link>
        <router-link to="/skills">技能</router-link>
        <router-link to="/cabinet">数据柜</router-link>
        <router-link to="/dashboard">看板</router-link>
      </nav>
      <!-- upload is an action, not a place: it stays a primary button reachable
           from every page instead of joining the navigation tabs -->
      <router-link to="/upload" class="upload-cta">
        <button class="primary">＋ 上传文档</button>
      </router-link>
      <div class="user-menu" @click.stop="menuOpen = !menuOpen">
        <span class="avatar">{{ initial }}</span>
        <span class="uname">{{ displayName }}</span>
        <div v-if="menuOpen" class="dropdown" @click.stop>
          <router-link v-if="isAdmin" to="/settings" @click="menuOpen = false">平台设置</router-link>
          <a href="#" @click.prevent="helpOpen = true; menuOpen = false">帮助</a>
          <template v-if="session.authRequired">
            <router-link to="/change-password" @click="menuOpen = false">修改密码</router-link>
            <a href="#" class="logout" @click.prevent="logout">退出登录</a>
          </template>
          <span v-else class="dim">免登录模式（lite/dev）</span>
        </div>
      </div>
    </header>

    <router-view />

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

const BARE = new Set(["/login", "/forgot", "/reset", "/activate", "/oidc"]);
const bare = computed(() => BARE.has(route.path));

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
.shell { min-height: 100vh; display: flex; flex-direction: column; }
.navbar { display: flex; align-items: center; gap: 28px; padding: 0 24px; height: 52px;
  background: var(--bg-panel); border-bottom: 1px solid var(--border);
  position: sticky; top: 0; z-index: 50; }
.logo { font-weight: 800; letter-spacing: 1px; color: var(--accent); font-size: 17px; }
nav { display: flex; gap: 4px; height: 100%; }
nav a { display: inline-flex; align-items: center; padding: 0 14px; color: var(--text);
  font-size: 14px; border-bottom: 2px solid transparent; }
nav a:hover { color: var(--accent); }
nav a.router-link-active { color: var(--accent); font-weight: 600;
  border-bottom-color: var(--accent); }
.upload-cta { margin-left: auto; display: inline-flex; }
.upload-cta button { padding: 5px 14px; font-size: 13px; }
.user-menu { display: flex; align-items: center; gap: 8px;
  cursor: pointer; position: relative; padding: 4px; }
.avatar { width: 28px; height: 28px; border-radius: 50%; background: var(--accent);
  color: var(--accent-text); display: inline-flex; align-items: center;
  justify-content: center; font-weight: 700; font-size: 13px; }
.uname { font-size: 13px; color: var(--text-dim); }
.dropdown { position: absolute; right: 0; top: 40px; background: var(--bg-raised);
  border: 1px solid var(--border); border-radius: 8px; min-width: 160px;
  display: flex; flex-direction: column; padding: 6px; z-index: 60; }
.dropdown a { padding: 8px 10px; border-radius: 6px; color: var(--text); font-size: 13px; }
.dropdown a:hover { background: var(--bg-panel); }
.dropdown .logout { color: var(--red); }
.dim { color: var(--text-dim); font-size: 12px; padding: 8px 10px; }

@media (max-width: 700px) {
  .navbar { gap: 10px; padding: 0 10px; overflow-x: auto; }
  nav a { padding: 0 8px; font-size: 13px; }
  .uname { display: none; }
  .upload-cta button { padding: 5px 10px; font-size: 12px; }
}
</style>
