/// <reference types="vitest/config" />
import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // Bind-mounted source on Docker Desktop (Windows/macOS) doesn't emit file events.
    watch: { usePolling: process.env.CHOKIDAR_USEPOLLING === 'true', interval: 300 },
  },
  build: {
    // Lightweight Charts is large and only needed on chart pages: keep it out of the entry chunk.
    rollupOptions: {
      output: { manualChunks: { charts: ['lightweight-charts'] } },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    restoreMocks: true,
    // Generous: CI runners and Docker Desktop VMs can be CPU-starved; a real hang still fails.
    testTimeout: 15_000,
  },
});
