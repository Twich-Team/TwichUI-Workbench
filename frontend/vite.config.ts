import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { fileURLToPath } from 'node:url';

export default defineConfig({
  root: fileURLToPath(new URL('.', import.meta.url)),
  plugins: [react(), tailwindcss()],
  server: {
    host: '127.0.0.1', port: Number(process.env.WORKBENCH_WEB_PORT || 5180), strictPort: true,
    proxy: { '/api': { target: `http://127.0.0.1:${process.env.WORKBENCH_API_PORT || 8787}`, changeOrigin: false } },
    fs: { strict: true, allow: [fileURLToPath(new URL('.', import.meta.url)), fileURLToPath(new URL('../node_modules', import.meta.url))] },
  },
  test: { environment: 'jsdom', setupFiles: ['./tests/setup.ts'], include: ['tests/**/*.test.tsx'] },
});
