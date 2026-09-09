/** Pure quarter-turn page geometry (WP2, 2026-09-09 plan §4): the single
 *  source of truth shared by DocStage's layout (per-page placeholder sizes,
 *  CSS transforms) and its pointer→UDR inverse mapping, so what is drawn and
 *  what is drawn on can never drift apart. Angles are CSS-style clockwise
 *  degrees; only the four quarter turns are meaningful.
 *
 *  Spaces:
 *  - UDR page space: [0..w]×[0..h] — the bbox coordinate base of results.
 *  - view space (unzoomed): the rotated bounding box [0..rw]×[0..rh] a page
 *    occupies after rotate(rot), translated into the non-negative quadrant:
 *    90° → (H−y, x); 180° → (W−x, H−y); 270° → (y, W−x).
 *  - screen: view space × zoom — exactly the placeholder div, which the page
 *    content renders into via `translate(t) rotate(rot) scale(zoom)`,
 *    transform-origin 0 0. */

export type Rotation = 0 | 90 | 180 | 270;

export function normRotation(deg: number): Rotation {
  return (((Math.round(deg) % 360) + 360) % 360) as Rotation;
}

/** Outer bounding size of a w×h page after the quarter-turn (no zoom). */
export function rotatedSize(w: number, h: number, rot: Rotation): { w: number; h: number } {
  return rot === 90 || rot === 270 ? { w: h, h: w } : { w, h };
}

/** UDR page coords → unzoomed rotated view coords (never negative). */
export function udrToView(x: number, y: number, w: number, h: number,
                          rot: Rotation): [number, number] {
  switch (rot) {
    case 90: return [h - y, x];
    case 180: return [w - x, h - y];
    case 270: return [y, w - x];
    default: return [x, y];
  }
}

/** Inverse of udrToView: unzoomed rotated view coords → UDR page coords. */
export function viewToUdr(vx: number, vy: number, w: number, h: number,
                          rot: Rotation): [number, number] {
  switch (rot) {
    case 90: return [vy, h - vx];
    case 180: return [w - vx, h - vy];
    case 270: return [w - vy, vx];
    default: return [vx, vy];
  }
}

/** The translate part of `translate(t) rotate(rot) scale(z)` (origin 0 0):
 *  shifts the rotated, scaled content into the placeholder's quadrant so the
 *  page never lands in unreachable negative coordinates. */
export function rotatedTranslate(w: number, h: number, rot: Rotation,
                                 z: number): [number, number] {
  switch (rot) {
    case 90: return [h * z, 0];
    case 180: return [w * z, h * z];
    case 270: return [0, w * z];
    default: return [0, 0];
  }
}

/** Fit-to-page zoom: the scale that fits the rotated page into the available
 *  viewport in both axes. Unbounded by design — a large page legitimately fits
 *  below 50% (0.5 is the manual-zoom clamp only, never the fit's floor). */
export function fitScale(availW: number, availH: number, w: number, h: number,
                         rot: Rotation): number | null {
  if (!(availW > 0) || !(availH > 0) || !(w > 0) || !(h > 0)) return null;
  const { w: rw, h: rh } = rotatedSize(w, h, rot);
  return Math.min(availW / rw, availH / rh);
}
