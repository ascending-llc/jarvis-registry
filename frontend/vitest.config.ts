import path from 'node:path';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
    // 'agent': silent for passing tests/files, full detail (diff, stack, code frame)
    // preserved for failures. Keeps a green run to a few summary lines.
    reporters: ['agent'],
  },
});
