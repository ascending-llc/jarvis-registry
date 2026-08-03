/** biome-ignore-all lint/style/useNodejsImportProtocol: <> */
import path from 'path';
import { defineConfig, loadEnv } from 'vite';

export default defineConfig(({ mode }) => {
  // Load env file from root directory
  const env = loadEnv(mode, path.resolve(__dirname, '..'), '');

  // For production: https://jarvis-demo.ascendingdc.com/gateway
  // For local dev: http://localhost:7860
  const backendURL = env.REGISTRY_URL || 'http://localhost:7860';

  // Auth server is at root /oauth2, not under registry path e.g. /gateway
  const authURL = env.AUTH_SERVER_EXTERNAL_URL || env.AUTH_SERVER_URL || 'http://localhost:8888';

  // The frontend image is built once, while NGINX_BASE_PATH is injected when the
  // container starts. Keep build assets relative and let the entrypoint add the
  // runtime <base href="..."> so deep links under /gateway load the right files.
  const assetBasePath = './';

  console.log('🔧 Vite Configuration:');
  console.log('  AUTH_SERVER_EXTERNAL_URL:', env.AUTH_SERVER_EXTERNAL_URL);
  console.log('  AUTH_SERVER_URL:', env.AUTH_SERVER_URL);
  console.log('  Resolved authURL:', authURL);
  console.log('  Backend URL:', backendURL);

  return {
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    // plugins: [
    //   react(),
    //   {
    //     name: 'mock-auth-config',
    //     configureServer(server) {
    //       server.middlewares.use((req, res, next) => {
    //         if (req.url === '/api/auth/config') {
    //           res.setHeader('Content-Type', 'application/json');
    //           res.end(JSON.stringify({ auth_server_url: authURL }));
    //           return;
    //         }
    //         next();
    //       });
    //     }
    //   }
    // ],
    base: assetBasePath,
    server: {
      port: 5173,
      host: '0.0.0.0',
      proxy: {
        '/oauth2': {
          target: authURL,
          changeOrigin: true,
          secure: false,
          cookieDomainRewrite: 'localhost',
          cookiePathRewrite: '/',
        },
        '/authorize': {
          target: authURL,
          changeOrigin: true,
          secure: false,
          cookieDomainRewrite: 'localhost',
          cookiePathRewrite: '/',
        },
        '/auth': {
          target: authURL,
          changeOrigin: true,
          secure: false,
          cookieDomainRewrite: 'localhost',
          cookiePathRewrite: '/',
        },
        '/redirect': {
          target: backendURL,
          changeOrigin: true,
          secure: false,
        },
        '/api': {
          target: backendURL,
          changeOrigin: true,
          secure: false,
        },
        '/proxy': {
          target: backendURL,
          changeOrigin: true,
          secure: false,
        },
        '/.well-known': {
          target: backendURL,
          changeOrigin: true,
          secure: false,
        },
        '/v0': {
          target: backendURL,
          changeOrigin: true,
          secure: false,
        },
        '/health': {
          target: backendURL,
          changeOrigin: true,
          secure: false,
        },
      },
    },
    build: {
      outDir: 'build',
      sourcemap: true,
    },
  };
});
