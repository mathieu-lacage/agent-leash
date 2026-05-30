import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  build: {
    outDir: '../src/sandbox/static',
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/api': 'http://localhost:7612',
      '/ws': { target: 'ws://localhost:7612', ws: true },
    },
  },
})
