import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [vue()],
  test: { environment: "jsdom", setupFiles: ["src/i18n/__tests__/setup.ts"], include: ["src/**/__tests__/**/*.test.ts"] },
});
