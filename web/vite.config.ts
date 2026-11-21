/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: false },
    },
  },
  preview: { port: 3000 },
  test: {
    environment: "jsdom",
    globals: false,
    setupFiles: ["./src/__tests__/setup.ts"],
    css: false,
  },
});
