import type { Directive } from "vue";

/** v-autosize (2026-10-30): a <textarea> grows with its text instead of
 *  scrolling inside a fixed box — the skill editor's 附加规则 runs to dozens of
 *  lines and was unreadable at 2 rows. `rows` stays the minimum height. */
const state = new WeakMap<HTMLTextAreaElement, { value: string; stop: () => void }>();

function fit(el: HTMLTextAreaElement) {
  // not laid out (closed <details>, inactive step): re-fit once it shows
  if (!el.offsetWidth) return;
  // collapsing to measure shortens the page for a moment and the browser
  // clamps any scroll position past the new end; put those back afterwards
  const scrolled: [Element, number][] = [];
  for (let n = el.parentElement; n; n = n.parentElement) {
    if (n.scrollTop) scrolled.push([n, n.scrollTop]);
  }
  el.style.height = "auto";
  // border-box (style.css): borders go on top of the content height
  el.style.height = `${el.scrollHeight + el.offsetHeight - el.clientHeight}px`;
  for (const [n, top] of scrolled) n.scrollTop = top;
  const s = state.get(el);
  if (s) s.value = el.value;
}

export const vAutosize: Directive<HTMLTextAreaElement> = {
  mounted(el) {
    el.style.overflowY = "hidden";        // a scrollbar would skew the wrap width
    let width = el.offsetWidth;
    // first show and re-wrap after the column width changes; height-only
    // changes (our own) are ignored. Deferred a frame: resizing inside the
    // callback trips Chrome's "ResizeObserver loop" error.
    const ro = new ResizeObserver(() => {
      if (el.offsetWidth === width) return;
      width = el.offsetWidth;
      requestAnimationFrame(() => fit(el));
    });
    ro.observe(el);
    const refit = () => fit(el);
    const details = el.closest("details");
    el.addEventListener("input", refit);
    details?.addEventListener("toggle", refit);
    state.set(el, { value: el.value, stop: () => {
      ro.disconnect();
      details?.removeEventListener("toggle", refit);
    } });
    fit(el);
  },
  // text replaced from code: switching versions, loading a template
  updated(el) { if (el.value !== state.get(el)?.value) fit(el); },
  beforeUnmount(el) { state.get(el)?.stop(); },
};
