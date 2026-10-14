import preact from '@preact/preset-vite'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  base: '/static/',
  plugins: [preact()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/accounts': 'http://127.0.0.1:8000',
      '/admin': 'http://127.0.0.1:8000',
    },
  },
})
