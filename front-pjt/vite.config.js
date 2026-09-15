import { fileURLToPath, URL } from 'node:url'

import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'

export function createVitePlugins(command, mode) {
  return [
    vue(),
    ...(command === 'serve' && mode === 'development' ? [vueDevTools()] : []),
  ]
}

// https://vite.dev/config/
export default defineConfig(({ command, mode }) => {
  const fileEnv = loadEnv(mode, process.cwd(), '')
  const proxyTarget = process.env.VITE_API_PROXY_TARGET || fileEnv.VITE_API_PROXY_TARGET

  return {
    plugins: createVitePlugins(command, mode),
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url))
      },
    },
    server: proxyTarget ? {
      allowedHosts: ['front'],
      proxy: {
        '/api': {
          target: proxyTarget,
          changeOrigin: true,
        },
      },
    } : undefined,
  }
})
