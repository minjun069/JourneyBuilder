import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'

export function createVitePlugins(command, mode) {
  return [
    vue(),
    ...(command === 'serve' && mode === 'development' ? [vueDevTools()] : []),
  ]
}

// https://vite.dev/config/
export default defineConfig(({ command, mode }) => ({
  plugins: createVitePlugins(command, mode),
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    },
  },
  server: process.env.VITE_API_PROXY_TARGET ? {
    allowedHosts: ['front'],
    proxy: {
      '/api': {
        target: process.env.VITE_API_PROXY_TARGET,
        changeOrigin: true,
      },
    },
  } : undefined,
}))
