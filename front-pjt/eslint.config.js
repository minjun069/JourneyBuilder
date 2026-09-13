import { defineConfig, globalIgnores } from 'eslint/config'
import globals from 'globals'
import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'
import pluginOxlint from 'eslint-plugin-oxlint'
import skipFormatting from 'eslint-config-prettier/flat'

export default defineConfig([
  {
    name: 'app/files-to-lint',
    files: ['**/*.{vue,js,mjs,jsx}'],
  },

  globalIgnores(['**/.vite/**', '**/dist/**', '**/dist-ssr/**', '**/coverage/**']),

  {
    name: 'app/browser-globals',
    files: ['src/**/*.{vue,js,jsx}'],
    languageOptions: {
      globals: {
        ...globals.browser,
      },
    },
  },

  {
    name: 'app/node-globals',
    files: ['*.config.{js,mjs}', '*.test.{js,mjs}', 'src/**/*.test.{js,mjs}', 'tests/**/*.{js,mjs}'],
    languageOptions: {
      globals: {
        ...globals.node,
      },
    },
  },

  js.configs.recommended,
  ...pluginVue.configs['flat/essential'],

  ...pluginOxlint.buildFromOxlintConfigFile('.oxlintrc.json'),

  skipFormatting,
])
