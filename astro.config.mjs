// @ts-check

import mdx from '@astrojs/mdx';
import sitemap from '@astrojs/sitemap';
import { unified } from '@astrojs/markdown-remark';
import { defineConfig, fontProviders } from 'astro/config';
import { sanitizePublicText } from './src/lib/sanitizePublicText.mjs';

function rehypePublicText() {
  /** @param {any} tree */
  return (tree) => {
    /** @param {any} node */
    const walk = (node) => {
      if (!node || typeof node !== 'object') return;
      if ((node.type === 'text' || node.type === 'raw') && typeof node.value === 'string') {
        node.value = sanitizePublicText(node.value);
      }
      // Also clean inline code that only carries tool names / paths
      if (node.type === 'element' && node.tagName === 'code' && Array.isArray(node.children)) {
        for (const child of node.children) {
          if (child?.type === 'text' && typeof child.value === 'string') {
            child.value = sanitizePublicText(child.value);
          }
        }
      }
      if (node.type === 'element' && node.tagName === 'a' && node.properties?.target === '_blank') {
        const rel = new Set(Array.isArray(node.properties.rel) ? node.properties.rel : []);
        rel.add('noopener');
        rel.add('noreferrer');
        node.properties.rel = [...rel];
      }
      if (Array.isArray(node.children)) {
        for (const child of node.children) walk(child);
      }
    };
    walk(tree);
  };
}

// https://astro.build/config
export default defineConfig({
  site: 'https://etf.peekabo.cc',
  integrations: [mdx(), sitemap()],
  markdown: {
    processor: unified({ rehypePlugins: [rehypePublicText] }),
  },
  fonts: [
    {
      provider: fontProviders.local(),
      name: 'Atkinson',
      cssVariable: '--font-atkinson',
      fallbacks: ['sans-serif'],
      options: {
        variants: [
          { src: ['./src/assets/fonts/atkinson-regular.woff'], weight: 400, style: 'normal', display: 'swap' },
          { src: ['./src/assets/fonts/atkinson-bold.woff'], weight: 700, style: 'normal', display: 'swap' },
        ],
      },
    },
  ],
});
