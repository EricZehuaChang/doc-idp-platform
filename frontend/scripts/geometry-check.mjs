/** WP2 geometry verification without a test framework (plan §5: verify the
 *  0/90/180/270 forward+inverse transforms with plain Node). Run:
 *
 *    node frontend/scripts/geometry-check.mjs
 *
 *  Transpiles src/documentGeometry.ts with the esbuild that ships inside
 *  vite's dependency tree — no new dependencies. Exits 1 on any failure. */
import { execFileSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const here = dirname(fileURLToPath(import.meta.url));
const require = createRequire(join(here, "noop.js"));
const esbuild = require("esbuild");

const tmp = mkdtempSync(join(tmpdir(), "idp-geom-"));
const outFile = join(tmp, "geometry.mjs");
esbuild.buildSync({
  entryPoints: [join(here, "..", "src", "documentGeometry.ts")],
  outfile: outFile, format: "esm", logLevel: "silent",
});
const g = await import(`file://${outFile}`);

let fails = 0;
function ok(cond, msg) {
  if (!cond) { console.error("FAIL:", msg); fails++; }
}

const EPS = 1e-9;
for (const rot of [0, 90, 180, 270]) {
  for (const [w, h] of [[595, 842], [842, 595], [1000, 750]]) {
    const { w: rw, h: rh } = g.rotatedSize(w, h, rot);
    const expected = rot % 180 === 0 ? [w, h] : [h, w];
    ok(rw === expected[0] && rh === expected[1],
      `rotatedSize rot=${rot} ${w}x${h} -> ${rw}x${rh}`);

    // every sampled point: forward lands in the non-negative quadrant, and
    // forward+inverse round-trips back to the exact UDR coordinate
    const pts = [[0, 0], [w, 0], [0, h], [w, h], [w / 2, h / 2], [37.5, 221.25]];
    for (const [x, y] of pts) {
      const [vx, vy] = g.udrToView(x, y, w, h, rot);
      ok(vx >= -EPS && vy >= -EPS, `view non-negative rot=${rot} (${x},${y})`);
      ok(vx <= rw + EPS && vy <= rh + EPS, `view inside box rot=${rot} (${x},${y})`);
      const [bx, by] = g.viewToUdr(vx, vy, w, h, rot);
      ok(Math.abs(bx - x) < EPS && Math.abs(by - y) < EPS,
        `round-trip rot=${rot} (${x},${y}) -> (${bx},${by})`);
    }

    // screen mapping: the CSS chain translate(t) rotate(rot) scale(z) applies
    // to the RAW rotated coords (which land in a negative quadrant); the
    // translate must cancel exactly that, so the screen position of any page
    // point equals its view coord × zoom and stays inside the placeholder —
    // no unreachable negative coordinates.
    const rawRot = (x, y) => rot === 90 ? [-y, x] : rot === 180 ? [-x, -y]
      : rot === 270 ? [y, -x] : [x, y];
    const z = 1.3;
    const [tx, ty] = g.rotatedTranslate(w, h, rot, z);
    for (const [x, y] of [[0, 0], [w, 0], [0, h], [w, h], [w / 2, h / 2]]) {
      const [rx, ry] = rawRot(x, y);
      const sx = rx * z + tx, sy = ry * z + ty;
      ok(sx >= -EPS && sx <= rw * z + EPS && sy >= -EPS && sy <= rh * z + EPS,
        `screen corner inside placeholder rot=${rot} (${x},${y}) -> (${sx},${sy})`);
      const [vx, vy] = g.udrToView(x, y, w, h, rot);
      ok(Math.abs(sx - vx * z) < EPS && Math.abs(sy - vy * z) < EPS,
        `screen == view×zoom rot=${rot} (${x},${y})`);
    }

    // fit scale makes the rotated page fill both axes without exceeding
    const f = g.fitScale(800, 600, w, h, rot);
    ok(f !== null && f > 0, `fit positive rot=${rot}`);
    ok(rw * f <= 800 + EPS && rh * f <= 600 + EPS, `fit contains rot=${rot}`);
    ok(Math.abs(rw * f - 800) < EPS || Math.abs(rh * f - 600) < EPS,
      `fit touches an axis rot=${rot}`);
  }
}

// degenerate inputs answer null instead of Infinity/NaN
ok(g.fitScale(0, 600, 100, 100, 0) === null, "fitScale zero width -> null");
ok(g.fitScale(800, 600, 0, 100, 0) === null, "fitScale zero page -> null");

// known values pinned from the plan
ok(JSON.stringify(g.rotatedSize(595, 842, 90)) === '{"w":842,"h":595}', "90° swaps dims");
ok(JSON.stringify(g.udrToView(0, 0, 100, 50, 90)) === "[50,0]", "top-left -> (H,0) at 90°");
ok(JSON.stringify(g.udrToView(0, 0, 100, 50, 270)) === "[0,100]", "top-left -> (0,W) at 270°");
ok(JSON.stringify(g.viewToUdr(0, 0, 100, 50, 90)) === "[0,50]", "90° inverse at origin");
ok(g.normRotation(-90) === 270 && g.normRotation(450) === 90, "normRotation wraps");

if (fails) {
  console.error(`geometry checks: ${fails} failed`);
  process.exit(1);
}
console.log("geometry checks: all passed (0/90/180/270 round-trips, placeholder containment, fit)");
