import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

/**
 * In development the SPA runs on 5173 and Django on 8000. Requests to /api are
 * proxied rather than sent cross-origin, which keeps the session cookie
 * first-party and means the dev setup exercises the same same-origin path
 * production uses. CORS is configured on the Django side as well, for anyone who
 * prefers to point VITE_API_BASE_URL straight at the API.
 */
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false },
    },
  },
  build: {
    // Served by Django in production; see the deployment note in the README.
    outDir: 'dist',
    sourcemap: true,
  },
});
