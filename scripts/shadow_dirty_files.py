#!/usr/bin/env python3
"""Single source of truth for cross-publisher shadow dirty-file exemptions.

Shadow / observation data files that cron jobs refresh daily (or intraday) and
that must never block any publisher's clean-worktree preflight. Each publisher
unions this set with its own owned-path exemptions, so adding a NEW shadow
source only requires editing THIS file — not every publisher's allowlist.

Files here are git-TRACKED (they appear in `git status --porcelain` as ` M`).
.gitignore-isolated shadow files (e.g. limit-up-shadow.json, mootdx-shadow.json)
do NOT appear in `git status` and therefore need no exemption at all.
"""
from __future__ import annotations

SHADOW_DIRTY_FILES = frozenset({
    # Shadow / observation data refreshed daily by cron — never blocks any publisher.
    "public/data/korea-tech-factor-shadow.json",
    "public/data/us-selector-shadow.json",
    "public/data/us-insider-ownership.json",
    # .gitignore-isolated A-share shadow snapshots (打板层 / mootdx).
    "public/data/limit-up-shadow.json",
    "public/data/mootdx-shadow.json",
    # A-share stage generated files — futures publisher must not be blocked
    # by dirty A-share artifacts (hit 2026-08-10 cascade).
    "public/data/etf-garden-backtest.json",
    "public/data/etf-garden-pool.json",
    "public/data/model-lab/a-share-shadow.json",
    "public/data/model-lab/a-share-path-shadow.json",
    "public/data/model-lab/a-share-research-audit.json",
    "public/data/a-share-nightly-deployment.json",
    "public/data/a-share-mid-macro.json",
    "public/data/a-compass-dashboard.json",
    "public/data/catalog.json",
    "public/data/garden-recommendations.json",
    "public/data/paper-trading.json",
    # US close publisher owns these as one deterministic snapshot family.
    "public/data/us-compass-health.json",
    "public/data/us-compass-learning.json",
    "public/data/us-compass-rotation-map.json",
    "public/data/us-compass-risk.json",
    "public/data/us-compass-shadow.json",
    "public/data/us-compass-research.json",
    "public/data/us-etf-backtest.json",
    "public/data/us-etf-flower-history.json",
    "public/data/us-etf-garden.json",
    "public/data/us-etf-pool.json",
    "public/data/us-macro-dashboard.json",
})
