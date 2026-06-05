import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');

  return {
    plugins: [
      react({
        // Treat .js files as JSX so existing .js components work without renaming
        include: ['**/*.jsx', '**/*.js'],
      }),
    ],

    define: {
      'process.env.REACT_APP_API_URL': JSON.stringify(
        env.VITE_API_URL || env.REACT_APP_API_URL || 'http://localhost:8000'
      ),
    },

    server: {
      port: 3000,
      proxy: {
        '/api': {
          target: env.VITE_API_URL || 'http://localhost:8000',
          changeOrigin: true,
          configure: (proxy) => {
            proxy.on('proxyRes', (proxyRes) => {
              if (proxyRes.headers['content-type']?.includes('text/event-stream')) {
                proxyRes.headers['x-accel-buffering'] = 'no';
              }
            });
          },
        },
      },
    },
  };
});
