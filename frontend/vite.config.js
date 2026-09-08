import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Paths owned by the backend API. A leading `^` makes Vite treat the key as a
// regex. This mirrors the proxy location in frontend/nginx.conf so `npm run dev`
// routes exactly like the Docker build — keep the two in sync.
//
// The /settings carve-out matters: the SPA owns the client-side route /settings
// while the API owns /settings/<resource>, so only sub-paths are proxied.
const API_ROUTES = '^(/auth|/leagues|/sync-status|/health)(/|$)|^/settings/.+'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // host: true so the dev server is reachable from a phone on the LAN too.
    host: true,
    port: 5173,
    proxy: {
      [API_ROUTES]: { target: 'http://localhost:8000', changeOrigin: false },
    },
  },
})
