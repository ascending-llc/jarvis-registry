import { useEffect, useRef } from 'react';
import { JarvisEmbed } from 'jarvis-embed';
import type { JarvisConfig } from 'jarvis-embed';

/**
 * Initializes a JarvisEmbed instance and cleans it up on unmount.
 * Pass null as config to skip initialization (e.g. before the user is authenticated).
 */
export function useJarvis(config: JarvisConfig | null) {
  const jarvisRef = useRef<JarvisEmbed | null>(null);

  useEffect(() => {
    if (!config) return;

    jarvisRef.current = new JarvisEmbed(config);

    return () => {
      jarvisRef.current?.destroy();
      jarvisRef.current = null;
    };
  }, [config]);

  return jarvisRef;
}
