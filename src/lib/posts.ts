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

export const cleanDescription = (text: string) => text
  .replace(/\bgenerated_at=[^，。]+[，,]\s*/gi, '')
  .replace(/\blatest_trade_date=[^，。]+[，,]\s*/gi, '')
  .replace(/\s+/g, ' ')
  .trim();

export const summarizeDescription = (text: string, maxLength = 110) => {
  const clean = cleanDescription(text);
  return clean.length > maxLength ? `${clean.slice(0, maxLength).replace(/[，、；：\s]+$/u, '')}…` : clean;
};
