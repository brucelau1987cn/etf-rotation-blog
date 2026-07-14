import type { CollectionEntry } from 'astro:content';

export type BlogPost = CollectionEntry<'blog'>;

export const isStockPost = (post: BlogPost) =>
  post.data.category === '个股' ||
  post.id.startsWith('stocks/') ||
  post.data.title.includes('个股') ||
  post.data.description.includes('个股');
export const isGardenSystemPost = (post: BlogPost) => post.id.startsWith('garden/');
export const isEtfResearchPost = (post: BlogPost) => post.id.startsWith('research/') || post.data.category === '研测';
export const isEtfPickPost = (post: BlogPost) => post.id.startsWith('picks/') || post.data.category === '研推';

export const isPublicEtfPost = (post: BlogPost) => !isStockPost(post) && !isGardenSystemPost(post);
export const isDailyReviewPost = (post: BlogPost) => isPublicEtfPost(post) && !isEtfResearchPost(post) && !isEtfPickPost(post);

export const sortByPubDateDesc = (a: BlogPost, b: BlogPost) => b.data.pubDate.valueOf() - a.data.pubDate.valueOf();

/** Public-facing compass vocabulary. Longer phrases first. */
export const renameCompassTerms = (text: string) => text
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

export const cleanDescription = (text: string) => renameCompassTerms(text)
  .replace(/\bgenerated_at=[^，。]+[，,]\s*/gi, '')
  .replace(/\blatest_trade_date=[^，。]+[，,]\s*/gi, '')
  .replace(/\s+/g, ' ')
  .trim();

export const summarizeDescription = (text: string, maxLength = 110) => {
  const clean = cleanDescription(text);
  return clean.length > maxLength ? `${clean.slice(0, maxLength).replace(/[，、；：\s]+$/u, '')}…` : clean;
};
