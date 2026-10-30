// Node 26 exposes an unconfigured native localStorage. Vitest keeps it when it
// populates globals, so take the actual browser store from its jsdom instance.
const testWindow = (globalThis as unknown as { jsdom: { window: Window & typeof globalThis } }).jsdom.window;
Object.defineProperty(globalThis, "localStorage", {
  configurable: true,
  value: testWindow.localStorage,
});
Object.defineProperty(globalThis, "Storage", {
  configurable: true,
  value: testWindow.Storage,
});
