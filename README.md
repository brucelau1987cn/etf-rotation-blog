# Astro Starter Kit: Blog

```sh
npm create astro@latest -- --template blog
```

> 🧑‍🚀 **Seasoned astronaut?** Delete this file. Have fun!

Features:

- ✅ Minimal styling (make it your own!)
- ✅ 100/100 Lighthouse performance
- ✅ SEO-friendly with canonical URLs and Open Graph data
- ✅ Sitemap support
- ✅ RSS Feed support
- ✅ Markdown & MDX support

## 🚀 Project Structure

Inside of your Astro project, you'll see the following folders and files:

```text
├── public/
├── src/
│   ├── assets/
│   ├── components/
│   ├── content/
│   ├── layouts/
│   └── pages/
├── astro.config.mjs
├── README.md
├── package.json
└── tsconfig.json
```

Astro looks for `.astro` or `.md` files in the `src/pages/` directory. Each page is exposed as a route based on its file name.

There's nothing special about `src/components/`, but that's where we like to put any Astro/React/Vue/Svelte/Preact components.

The `src/content/` directory contains "collections" of related Markdown and MDX documents. Use `getCollection()` to retrieve posts from `src/content/blog/`, and type-check your frontmatter using an optional schema. See [Astro's Content Collections docs](https://docs.astro.build/en/guides/content-collections/) to learn more.

Any static assets, like images, can be placed in the `public/` directory.

## 🧞 Commands

All commands are run from the root of the project, from a terminal:

| Command                   | Action                                           |
| :------------------------ | :----------------------------------------------- |
| `npm install`             | Installs dependencies                            |
| `npm run dev`             | Starts local dev server at `localhost:4321`      |
| `npm run build`           | Build your production site to `./dist/`          |
| `npm run preview`         | Preview your build locally, before deploying     |
| `npm run astro ...`       | Run CLI commands like `astro add`, `astro check` |
| `npm run astro -- --help` | Get help using the Astro CLI                     |

## 👀 Want to learn more?

Check out [our documentation](https://docs.astro.build) or jump into our [Discord server](https://astro.build/chat).

## Data sources

| Page | Source | Script |
| --- | --- | --- |
| `/momentum` ETF 动量雷达 | `public/data/etf-garden-pool.json` (stock-api v2.7.2 + youth-online mirror) | `scripts/generate_garden_pool.py` |
| `/c09-pulse` C09 竞价狙击 | `public/data/c09-signal.json` (nasa-drain-arthritis-figured.trycloudflare.com trycloudflare tunnel) | `scripts/fetch_c09_signal.py` |
| `/garden` ETF 花园 | `public/data/etf-garden-pool.json` | `scripts/generate_garden_pool.py` |
| `/stocks` 个股深度 | `src/content/blog/stocks/*.md` | hand-written |

### C09 实验室

`scripts/fetch_c09_signal.py` 调 `trycloudflare.com` 临时隧道（24-72h 失效）拉 2 个端点：

- `GET /api/live-news?ts=0` — 4 天窗口新闻 + S/A 评级 + 板块星图
- `GET /api/current-signal?ts=0` — ORION 决策中枢 TOP3（仅交易日有信号）

输出原子化写到 `public/data/c09-signal.json`，构建时由 `/c09-pulse` 页面读取并渲染。
失败时**保留上次快照**（不覆盖），不破坏已部署的页面。

## Credit

This theme is based off of the lovely [Bear Blog](https://github.com/HermanMartinus/bearblog/).
