<template>
  <button ref="triggerEl" class="dots" type="button"
          aria-label="更多操作" aria-haspopup="menu" :aria-expanded="open"
          @click.stop="toggle">
    ⋯
  </button>
  <Teleport to="body">
    <div v-if="open" ref="menuEl" class="rowmenu" role="menu" :style="pos">
      <button v-for="(it, i) in items" :key="it.key" role="menuitem" type="button"
              class="rowmenu-item" :class="{ danger: it.danger, focus: i === cursor }"
              @click.stop="pick(it)" @mousemove="cursor = i">
        {{ it.label }}
      </button>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
/** 9.15 R22: row actions collapse into one "⋯" trigger (表格行不再摆一排按钮).
 *  Click to open (hover-open mis-fires when scrolling the table); the menu is
 *  teleported + position:fixed with flip/收边 so it survives the last row, the
 *  right edge and narrow windows; Esc / outside click / scroll / resize close
 *  it; ↑↓ + Enter drive it from the keyboard. Menu clicks stop propagation so
 *  a row's @click navigation never fires. */
import { onMounted, onUnmounted, ref } from "vue";

export interface RowMenuItem {
  key: string;
  label: string;
  danger?: boolean;
}

const props = defineProps<{ items: RowMenuItem[] }>();
const emit = defineEmits<{ (e: "select", key: string): void }>();

const open = ref(false);
const cursor = ref(0);
const triggerEl = ref<HTMLElement>();
const menuEl = ref<HTMLElement>();
const pos = ref<Record<string, string>>({});
const MENU_W = 148;
const ITEM_H = 32;

function toggle() {
  open.value ? close() : show();
}

function show() {
  cursor.value = 0;
  open.value = true;
  // measure after mount so the flip decision uses the real height
  requestAnimationFrame(() => {
    const r = triggerEl.value?.getBoundingClientRect();
    const h = (props.items.length * ITEM_H) + 12;
    if (!r) return;
    const below = r.bottom + 6;
    const top = below + h > window.innerHeight - 8 ? r.top - h - 6 : below; // 上翻
    const left = Math.max(8, Math.min(r.right, window.innerWidth - 8) - MENU_W); // 左收
    pos.value = { top: `${Math.max(8, top)}px`, left: `${left}px`, width: `${MENU_W}px` };
    menuEl.value?.focus();
  });
}

function close() {
  open.value = false;
}

function pick(it: RowMenuItem) {
  close();
  emit("select", it.key);
}

function onEsc(e: KeyboardEvent) {
  if (e.key === "Escape") close();
}
function onKey(e: KeyboardEvent) {
  if (!open.value) return;
  if (e.key === "ArrowDown") {
    e.preventDefault();
    cursor.value = (cursor.value + 1) % props.items.length;
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    cursor.value = (cursor.value - 1 + props.items.length) % props.items.length;
  } else if (e.key === "Enter") {
    e.preventDefault();
    const it = props.items[cursor.value];
    if (it) pick(it);
  }
}
function onDocClick(e: MouseEvent) {
  if (!open.value) return;
  if (menuEl.value?.contains(e.target as Node)) return;
  if (triggerEl.value?.contains(e.target as Node)) return;
  close();
}
function onScroll(e: Event) {
  // a scroll inside the menu (rare, overflow kept hidden) must not close it
  if (menuEl.value && e.target instanceof Node && menuEl.value.contains(e.target)) return;
  close();
}

onMounted(() => {
  document.addEventListener("click", onDocClick);
  window.addEventListener("keydown", onKey);
  window.addEventListener("keydown", onEsc);
  window.addEventListener("resize", close);
  window.addEventListener("scroll", onScroll, true);
});
onUnmounted(() => {
  document.removeEventListener("click", onDocClick);
  window.removeEventListener("keydown", onKey);
  window.removeEventListener("keydown", onEsc);
  window.removeEventListener("resize", close);
  window.removeEventListener("scroll", onScroll, true);
});
</script>

<style scoped>
.dots {
  padding: 2px 10px;
  font-size: 14px;
  line-height: 1.4;
  letter-spacing: 1px;
}
</style>

<style>
/* teleported to body, so not scoped */
.rowmenu { position: fixed; z-index: 210; background: var(--bg-raised);
  border: 1px solid var(--border); border-radius: 8px; padding: 6px;
  display: flex; flex-direction: column; box-shadow: 0 8px 24px rgba(0,0,0,.45);
  outline: none; }
.rowmenu-item { text-align: left; background: transparent; border: none;
  border-radius: 5px; padding: 7px 10px; font-size: 12.5px; color: var(--text);
  cursor: pointer; white-space: nowrap; }
.rowmenu-item:hover, .rowmenu-item.focus { background: rgba(240, 180, 41, 0.10); }
.rowmenu-item.danger { color: var(--red); }
</style>
