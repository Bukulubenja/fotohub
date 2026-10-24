/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In development the dashboard and the API share an origin through this proxy, so the
// Django session cookie and CSRF checks work without any CORS setup.
const DJANGO = process.env.DJANGO_ORIGIN ?? 'http://127.0.0.1:8000'

export default defineConfig(({ command }) => ({
  // Built assets are collected by Django as static files under /static/dashboard/.
  base: command === 'build' ? '/static/dashboard/' : '/',
  plugins: [react()],
  server: {
    // /g/ is the customer gallery: share links and previews then work from the dashboard.
    proxy: { '/api': { target: DJANGO }, '/g': { target: DJANGO } },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
  },
}))
