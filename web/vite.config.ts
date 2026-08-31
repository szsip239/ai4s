import path from 'path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react-swc';
// import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite';
import tanstackRouter from '@tanstack/router-plugin/vite';
import { VitePWA } from 'vite-plugin-pwa';

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    tanstackRouter({
      target: 'react',
      autoCodeSplitting: true,
    }),
    react(),
    // React table does not work with react-compiler, disable for now.
    // react({
    //   babel: {
    //     plugins: ['babel-plugin-react-compiler'],
    //   },
    // }),
    tailwindcss(),
    // PWA：manifest 手写于 public/manifest.webmanifest；SW 仅做静态资源预缓存 + SPA 离线壳，
    // API（/admin /v1 /self /dlp-admin /oauth）一律走网络，不做缓存——管理后台数据必须实时。
    VitePWA({
      registerType: 'autoUpdate',
      manifest: false,
      includeAssets: ['favicon.ico', 'logo.svg', 'manifest.webmanifest'],
      workbox: {
        navigateFallback: '/index.html',
        navigateFallbackDenylist: [/^\/(admin|v1|self|dlp-admin|oauth)\//],
        globPatterns: ['**/*.{js,css,html,svg,png,ico,woff2}'],
        maximumFileSizeToCacheInBytes: 6 * 1024 * 1024,
        runtimeCaching: [
          {
            urlPattern: /\/(admin|v1|self|dlp-admin|oauth)\//,
            handler: 'NetworkOnly',
          },
          {
            urlPattern: /^https:\/\/fonts\.(googleapis|gstatic)\.com\//,
            handler: 'CacheFirst',
            options: {
              cacheName: 'google-fonts',
              expiration: { maxEntries: 20, maxAgeSeconds: 60 * 60 * 24 * 365 },
            },
          },
        ],
      },
    }),
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),

      // fix loading all icon chunks in dev mode
      // https://github.com/tabler/tabler-icons/issues/1233
      '@tabler/icons-react': '@tabler/icons-react/dist/esm/icons/index.mjs',
    },
  },
  server: {
    port: process.env.VITE_PORT ? parseInt(process.env.VITE_PORT) : 5173,
    proxy: {
      '/admin': {
        target: process.env.VITE_API_URL || 'http://localhost:3000',
        changeOrigin: true,
      },
      '/oauth': {
        target: process.env.VITE_API_URL || 'http://localhost:3000',
        changeOrigin: true,
        bypass: (req) => {
          if (req.url?.includes('idp-callback')) {
            return req.url;
          }
        },
      },
      '/v1': {
        target: process.env.VITE_API_URL || 'http://localhost:3000',
        changeOrigin: true,
      },
      // 员工自助平面（issue #74 评审 P2）：经本地网关到 shim /self/*
      '/self': {
        target: process.env.VITE_API_URL || 'http://localhost:3000',
        changeOrigin: true,
      },
      // DLP 管理平面（issue #120 补既有缺口）：经本地网关到 shim /dlp-admin/*（脱敏规则/智能路由页同源调用）
      '/dlp-admin': {
        target: process.env.VITE_API_URL || 'http://localhost:3000',
        changeOrigin: true,
      },
    },
  },
});
