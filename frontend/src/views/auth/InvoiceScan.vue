<template>
  <figure>
    <svg :key="playback" class="document" aria-hidden="true" focusable="false" width="520" height="320" viewBox="0 0 520 320" fill="none">
      <defs>
        <pattern id="dots" width="14" height="14" patternUnits="userSpaceOnUse">
          <circle cx="7" cy="7" r=".8" fill="#c1cbd1" opacity=".22"/>
        </pattern>
        <linearGradient id="paper" x1=".1" y1="0" x2=".9" y2="1">
          <stop stop-color="#fffdf3"/>
          <stop offset=".7" stop-color="#f3eddd"/>
          <stop offset="1" stop-color="#e6ddc9"/>
        </linearGradient>
        <linearGradient id="fold" x1="0" y1="1" x2="1" y2="0">
          <stop stop-color="#d0c5ae"/>
          <stop offset="1" stop-color="#f6efd9"/>
        </linearGradient>
        <clipPath id="invoice-sheet">
          <path d="M103 20h214l18 18v251H103z" />
        </clipPath>
        <linearGradient id="scan-light" x1="0" y1="0" x2="0" y2="1">
          <stop stop-color="#f0b429" stop-opacity="0" />
          <stop offset=".8" stop-color="#f0b429" stop-opacity=".24" />
          <stop offset="1" stop-color="#ffcf64" stop-opacity=".76" />
        </linearGradient>
        <clipPath id="invoice-trace">
          <rect class="trace-mask" x="324" y="200" width="50" height="34" />
        </clipPath>
      </defs>

      <!-- Scanner registration bed. -->
      <rect x="31" y="24" width="452" height="266" fill="url(#dots)"/>
      <g stroke="#819094" stroke-opacity=".67">
        <path d="M26 35h6m-3-3v6m437-3h6m-3-3v6M26 283h6m-3-3v6m437-3h6m-3-3v6"/>
      </g>

      <!-- A single ledger sheet, lifted slightly from the scanning surface. -->
      <g transform="rotate(-7 214 157)">
        <path d="M109 28h214l18 18v250H109z" fill="#0b141d" opacity=".5"/>
        <path d="M103 20h214l18 18v251H103z" fill="url(#paper)"/>
        <path d="M317 20l18 18h-18z" fill="url(#fold)"/>
        <g fill="#293338" font-family="Arial, Helvetica, sans-serif">
          <path fill-rule="evenodd" d="m122 46 8-13 8 13h-4l-4-7-4 7zm1 3h14v2h-14z"/>
          <text x="145" y="46" font-size="8.5" font-weight="bold" letter-spacing=".65">ASTER PRESS</text>
          <path d="M121 60h195" stroke="#83908c" stroke-width=".7"/>
          <text x="120" y="96" font-size="32" font-family="Georgia, 'Times New Roman', serif" letter-spacing="-1.6">Invoice</text>
          <text x="122" y="112" font-size="7.5" font-family="Menlo, Consolas, monospace" letter-spacing=".3">INV–0248</text>

          <g fill="#626b66" font-size="6.5" letter-spacing=".8">
            <text x="122" y="138">BILL TO</text>
            <text x="239" y="138">ISSUED</text>
          </g>
          <g font-size="9">
            <text x="122" y="151">Palmer Studio</text>
            <text x="239" y="151">08 Apr 2026</text>
          </g>

          <path d="M121 167h195M121 185h195M121 221h195" stroke="#a4a69a" stroke-width=".65"/>
          <g fill="#626b66" font-size="6.5" letter-spacing=".6">
            <text x="122" y="179">DESCRIPTION</text>
            <text x="316" y="179" text-anchor="end">USD</text>
          </g>
          <g font-size="8">
            <text x="122" y="199">Edition printing</text>
            <text x="122" y="214">Paper stock</text>
            <text x="316" y="199" text-anchor="end">3,600.00</text>
            <text x="316" y="214" text-anchor="end">680.00</text>
          </g>
          <text x="122" y="245" font-size="9" font-weight="bold">Total</text>
          <g class="field-lock">
            <path d="M244 229h76v22h-76z" fill="#edb647" fill-opacity=".23" stroke="#b2862e" stroke-width=".65"/>
            <path d="M244 235v-6h6m64 0h6v6m0 10v6h-6m-64 0h-6v-6" fill="none" stroke="#986b16" stroke-width="1.5"/>
          </g>
          <text x="316" y="245" font-size="12.5" font-weight="bold" text-anchor="end">$4,280.00</text>

          <path d="M121 265h195" stroke="#a4a69a" stroke-width=".65"/>
          <g font-size="6" fill="#626b66" letter-spacing=".65">
            <text x="122" y="278">ASTER PRESS</text>
            <text x="316" y="278" text-anchor="end">1 / 1</text>
          </g>
        </g>
        <g clip-path="url(#invoice-sheet)">
          <g class="scanner">
            <rect x="103" y="-18" width="232" height="38" fill="url(#scan-light)" />
            <path d="M103 20h232" stroke="#b67a10" stroke-width="2.5" />
            <path d="M103 19h232" stroke="#fff0b0" stroke-width="1.25" />
          </g>
        </g>
      </g>

      <!-- The traced field and its extracted value share one familiar accent. -->
      <g clip-path="url(#invoice-trace)" stroke="#eabb59" stroke-width="1.2">
        <path d="M329 228h16l17-22h9"/>
        <circle cx="329" cy="228" r="2.4" fill="#efc162"/>
      </g>
      <g transform="translate(370 174)">
        <g class="result">
          <rect width="130" height="57" rx="4" fill="#15202a" stroke="#ae8c50"/>
          <path d="M12 13h5m-5 0v5m10-5h5v5m0 5v4h-5m-5 0h-5v-4" stroke="#eabe68" stroke-width="1"/>
          <path d="m16 20 3 3 5-7" stroke="#edc26e" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>
          <text x="37" y="24" fill="#d8ba7c" font-family="Menlo, Consolas, monospace" font-size="8.5" letter-spacing="1">TOTAL</text>
          <text x="12" y="46" fill="#faf4e7" font-family="Menlo, Consolas, monospace" font-size="16" letter-spacing="-.8">$4,280.00</text>
        </g>
      </g>
    </svg>
    <figcaption>
      <span>{{ $t('login.figure') }}</span>
      <button type="button" class="replay" @click="playback++">
        <svg aria-hidden="true" width="15" height="15" viewBox="0 0 24 24" fill="none"
          stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
          <path d="M3 10a9 9 0 1 1 1.4 7M3 4v6h6" />
        </svg>
        {{ $t('login.replay') }}
      </button>
    </figcaption>
  </figure>
</template>

<script setup lang="ts">
import { ref } from "vue";

const playback = ref(0);
</script>

<style scoped>
figure { margin: clamp(24px, 4vh, 48px) 0 0; }
.document { display: block; width: 100%; height: auto; }
.scanner { opacity: 0; }
figcaption {
  margin-top: 14px; padding-top: 9px; border-top: 1px solid #47535c;
  display: flex; align-items: center; justify-content: space-between; min-height: 40px;
  gap: 12px; color: #c0c9cf; font-size: 12px;
}
.replay {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 5px 8px; border: 1px solid #65717b; border-radius: 5px;
  background: transparent; color: #ece8df; font: inherit;
}
.replay:focus-visible { outline: 2px solid #f0b429; outline-offset: 3px; }
@media (hover: hover) and (pointer: fine) {
  .replay:hover { color: #fff; border-color: #eabb59; }
}
@media (min-width: 761px) and (prefers-reduced-motion: no-preference) {
  .scanner { animation: scan 1.65s linear .18s both; }
  .field-lock {
    transform-box: fill-box; transform-origin: center;
    animation: lock .24s ease-out 1.49s backwards;
  }
  .trace-mask {
    transform-box: fill-box; transform-origin: left center;
    animation: trace .35s ease-in-out 1.72s backwards;
  }
  .result { animation: reveal .3s ease-out 2.04s backwards; }
}
@keyframes scan {
  0% { opacity: 0; transform: translateY(0); }
  9%, 88% { opacity: 1; }
  100% { opacity: 0; transform: translateY(269px); }
}
@keyframes lock {
  from { opacity: 0; transform: scale(1.12); }
  to { opacity: 1; transform: scale(1); }
}
@keyframes trace { from { transform: scaleX(0); } to { transform: scaleX(1); } }
@keyframes reveal {
  from { opacity: 0; transform: translateX(-6px); }
  to { opacity: 1; transform: translateX(0); }
}
@media (prefers-reduced-motion: reduce) {
  .replay { display: none; }
}
</style>
