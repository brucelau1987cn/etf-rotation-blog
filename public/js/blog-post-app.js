/**
 * Blog article client: public-text sanitize, longform toggle, TOC.
 */
      const sanitizePublicText = (text) => {
        if (!text) return text;
        let s = String(text)
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
        const pairs = [
          [/MX-Skills\s*mx-data/gi, '资金数据'],
          [/MX-Skills\s*f\d+/gi, '资金复核'],
          [/MX-Skills/gi, '资金数据'],
          [/MX-Search/gi, '资讯检索'],
          [/MX资金终检/g, '资金终检'],
          [/MX资金/g, '资金'],
          [/\bmx-data\b/gi, '资金数据'],
          [/\bmx-skills\b/gi, '资金数据'],
          [/\bmx-search\b/gi, '资讯检索'],
          [/iWenCai\s*hithink-market-query/gi, '指数交叉'],
          [/iWenCai\s*news-search/gi, '新闻检索'],
          [/同花顺问财/g, '公开资讯'],
          [/iWenCai/gi, '公开资讯'],
          [/\biwencai\b/gi, '公开资讯'],
          [/hithink-market-query/gi, '指数交叉'],
          [/\bhithink\b/gi, '公开资讯'],
          [/stock-api(?:@[\d.]+)?(?:\s*package)?(?:\s*v?[\d.]+)?/gi, '行情接口'],
          [/stock-price-query/gi, '行情查询'],
          [/web\.ifzq\.gtimg\.cn/gi, '公开日K'],
          [/qt\.gtimg\.cn/gi, '公开行情'],
          [/gtimg\.cn/gi, '公开行情'],
          [/腾讯行情快照/g, '公开行情快照'],
          [/腾讯日K接口/g, '公开日K'],
          [/腾讯日K/g, '公开日K'],
          [/Tencent\s*行情/gi, '公开行情'],
          [/\bTencent\b/g, '公开行情'],
          [/moomoo\/Futu\s*OpenD/gi, '港股行情通道'],
          [/Futu\s*OpenD/gi, '港股行情通道'],
          [/OpenD/g, '港股行情通道'],
          [/moomoo/gi, '港股行情'],
          [/富途新闻检索/g, '资讯检索'],
          [/富途新闻/g, '资讯'],
          [/\bFutu\b/g, '资讯通道'],
          [/yfinance/gi, '美股行情'],
          [/Yahoo\s*Finance/gi, '美股行情'],
          [/\bYahoo\b/g, '美股行情'],
          [/public\/data\/etf-garden-pool\.json/g, '本地A股池快照'],
          [/etf-garden-pool\.json/g, '本地A股池快照'],
          [/rawTable/g, '资金表'],
          [/named-key\s*表/g, '资金表'],
          [/Bruce ETF Trend Radar v3/g, 'ETF趋势雷达 v3'],
        ];
        for (const [re, rep] of pairs) s = s.replace(re, rep);
        return s.replace(/\s{2,}/g, ' ').trim();
      };
      const content = document.querySelector('#article-content');
      const longformToggle = document.querySelector('#longform-toggle');
      const expandLongform = () => {
        if (!content || !longformToggle) return;
        content.classList.remove('longform-collapsed');
        longformToggle.textContent = '收起完整正文';
        longformToggle.setAttribute('aria-expanded', 'true');
      };
      if (content && longformToggle) {
        longformToggle.addEventListener('click', () => {
          const collapsed = content.classList.toggle('longform-collapsed');
          longformToggle.textContent = collapsed ? '展开完整正文' : '收起完整正文';
          longformToggle.setAttribute('aria-expanded', String(!collapsed));
          if (collapsed) document.querySelector('.longform-tools')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        });
        if (location.hash) expandLongform();
        window.addEventListener('hashchange', expandLongform);
      }
      if (content) {
        const walker = document.createTreeWalker(content, NodeFilter.SHOW_TEXT);
        const nodes = [];
        while (walker.nextNode()) nodes.push(walker.currentNode);
        for (const node of nodes) {
          const next = sanitizePublicText(node.nodeValue || '');
          if (next !== node.nodeValue) node.nodeValue = next;
        }
      }
      const toc = document.querySelector('#article-toc');
      if (content && toc) {
        const headings = [...content.querySelectorAll('h2, h3')].filter((heading) => heading.id);
        toc.innerHTML = headings.length
          ? headings.map((heading) => `<a class="${heading.tagName === 'H3' ? 'sub' : ''}" href="#${heading.id}">${heading.textContent || ''}</a>`).join('')
          : '<span class="last-updated-on">本文暂无分节目录</span>';
        const links = [...toc.querySelectorAll('a')];
        if ('IntersectionObserver' in window) {
          const observer = new IntersectionObserver((entries) => {
            const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
            if (!visible) return;
            links.forEach((link) => link.classList.toggle('active', link.getAttribute('href') === `#${visible.target.id}`));
          }, { rootMargin: '-18% 0px -72% 0px' });
          headings.forEach((heading) => observer.observe(heading));
        }
        content.querySelectorAll('table').forEach((table) => {
          const hint = document.createElement('p');
          hint.className = 'table-hint';
          hint.textContent = '表格可左右滑动查看完整内容';
          table.before(hint);
        });
      }
