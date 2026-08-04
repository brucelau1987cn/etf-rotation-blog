export type RollingInsightArticle = {
  symbol: string;
  name: string;
  initials: string;
  tradeDate: string;
  href: string;
};

// Legacy export kept for type imports; single-stock articles have been merged into daily reports.
export const rollingInsightArticles: RollingInsightArticle[] = [];
