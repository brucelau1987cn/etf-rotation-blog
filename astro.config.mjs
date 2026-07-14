// @ts-check

import mdx from '@astrojs/mdx';
import sitemap from '@astrojs/sitemap';
import { defineConfig, fontProviders } from 'astro/config';

/** Public-facing compass vocabulary for historical garden wording. */
function renameCompassTerms(text) {
  return text
    .replaceAll('观察不种', '仅观察不升级')
    .replaceAll('准备种花', '候场')
    .replaceAll('准备摘花', '止盈观察')
    .replaceAll('失效退出', '破位撤退')
    .replaceAll('07:30早盘版', '08:30盘前版')
    .replaceAll('07:30 早盘预测', '08:30 盘前预测')
    .replaceAll('07:30早盘预测', '08:30盘前预测')
    .replaceAll('07:30准备信号', '08:30准备信号')
    .replaceAll('ETF花园', 'ETF罗盘')
    .replaceAll('花园信号', '罗盘信号')
    .replaceAll('回踩位', '伏击位')
    .replaceAll('目标位', '兑现位')
    .replaceAll('失效线', '防守线')
    .replaceAll('种花', '伏击')
    .replaceAll('摘花', '兑现');
}

function rehypeCompassTerms() {
  return (tree) => {
    const walk = (node) => {
      if (!node || typeof node !== 'object') return;
      if (node.type === 'text' && typeof node.value === 'string') {
        node.value = renameCompassTerms(node.value);
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
    rehypePlugins: [rehypeCompassTerms],
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
