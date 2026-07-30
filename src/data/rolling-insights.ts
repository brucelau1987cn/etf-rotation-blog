export type RollingInsightArticle = {
  symbol: string;
  name: string;
  initials: string;
  tradeDate: string;
  href: string;
};

export const rollingInsightArticles: RollingInsightArticle[] = [
  {
    symbol: '002173',
    name: '创新医疗',
    initials: 'cxyl',
    tradeDate: '2026-07-30',
    href: '/rolling/insights/',
  },
];
