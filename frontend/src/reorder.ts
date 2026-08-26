// Pointer-based list reordering.
//
// Deliberately NOT the HTML5 drag-and-drop API: dragstart never fires from a
// touch, and a long field list needs the page to auto-scroll while the item is
// in flight — neither is workable with native DnD. Pointer events give one code
// path for mouse, pen and touch, and let the drop target be computed from live
// geometry so the indicator always matches where the row will land.
import { ref } from "vue";

export interface Reorder {
  from: ReturnType<typeof ref<number>>;
  over: ReturnType<typeof ref<number>>;
  start: (index: number, ev: PointerEvent, container: HTMLElement | undefined,
          commit: (from: number, to: number) => void) => void;
}

const EDGE = 60;        // px from the viewport edge where auto-scroll kicks in
const SPEED = 12;

export function useListReorder(): Reorder {
  const from = ref(-1);
  const over = ref(-1);

  function start(index: number, ev: PointerEvent, container: HTMLElement | undefined,
                 commit: (from: number, to: number) => void) {
    if (!container || ev.button !== 0) return;
    ev.preventDefault();                       // no text selection while dragging
    from.value = index;
    over.value = index;
    const grip = ev.currentTarget as HTMLElement;
    grip.setPointerCapture(ev.pointerId);

    let raf = 0;
    let edgeDir = 0;
    const autoScroll = () => {
      if (edgeDir) window.scrollBy(0, edgeDir * SPEED);
      raf = edgeDir ? requestAnimationFrame(autoScroll) : 0;
    };

    const cards = () => [...container.children].filter(
      (el) => el.classList.contains("fc")) as HTMLElement[];

    function onMove(e: PointerEvent) {
      // drop slot = the card whose vertical midpoint the pointer has passed
      const list = cards();
      let target = list.length - 1;
      for (let i = 0; i < list.length; i++) {
        const r = list[i].getBoundingClientRect();
        if (e.clientY < r.top + r.height / 2) { target = i; break; }
      }
      over.value = target;

      edgeDir = e.clientY < EDGE ? -1 : e.clientY > window.innerHeight - EDGE ? 1 : 0;
      if (edgeDir && !raf) raf = requestAnimationFrame(autoScroll);
    }
    function onUp() {
      edgeDir = 0;
      if (raf) { cancelAnimationFrame(raf); raf = 0; }
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      const f = from.value ?? -1;
      const t = over.value ?? -1;
      from.value = -1;
      over.value = -1;
      if (f >= 0 && t >= 0 && f !== t) commit(f, t);
    }
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
  }

  return { from, over, start };
}

/** In-place move used by every reorderable list (fields, table columns). */
export function moveItem<T>(list: T[], from: number, to: number): void {
  if (from < 0 || to < 0 || from === to || from >= list.length) return;
  const [item] = list.splice(from, 1);
  list.splice(Math.min(to, list.length), 0, item);
}
