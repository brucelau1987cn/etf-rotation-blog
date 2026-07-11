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
| `/garden` ETF 花园 | `public/data/etf-garden-pool.json` | `scripts/generate_garden_pool.py` |
| `/paper` ETF 虚拟交易 | `public/data/paper-trading.json`（运行态位于仓库外） | `scripts/paper_trade_runner.py` |
| `/stocks` 个股深度 | `src/content/blog/stocks/*.md` | hand-written |

## Paper trading

The stdlib-only runner keeps private mutable state outside the repository and exports a public snapshot for `/paper/`.

```sh
# Initialize one account (A: CNY 150,000; US: USD 20,000)
python3 scripts/paper_trade_runner.py --market A --mode init
# Poll during market hours; close marks NAV/history and exports public JSON
python3 scripts/paper_trade_runner.py --market A --mode intraday
python3 scripts/paper_trade_runner.py --market A --mode close
# Safe local checks
python3 scripts/paper_trade_runner.py --self-test
python3 -m unittest discover -s tests -v
```

Use `--state PATH`, `--now ISO_TIMESTAMP`, or `--dry-run` as needed. Default state is `/root/.hermes/state/etf-paper-trading.json`, written atomically under `flock`. A-share quotes use Tencent; US quotes use Yahoo Finance 5-minute bars. `scripts/publish_paper_trading.py --market A|US` performs close/export/build and then stages, commits only when the public snapshot changed, and pushes; its side effects are intentional, so use it only in publishing automation.

Rules: maximum 10% per position and 10 positions; cash reserves are 20% (A) and 15% (US); A shares use 100-unit lots, US shares integers. Only exact formal plant signals buy, ready signals never trade, held positions retain entry-time target/stop, and same-bar stop risk precedes targets and source exits. Simulated costs are A 0.025% commission (minimum CNY5) plus 0.05% slippage and US minimum USD1 commission plus 0.05% slippage.

## Credit

This theme is based off of the lovely [Bear Blog](https://github.com/HermanMartinus/bearblog/).
