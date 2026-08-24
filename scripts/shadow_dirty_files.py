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
    "public/data/korea-tech-factor-shadow.json",
    "public/data/us-selector-shadow.json",
    "public/data/us-insider-ownership.json",
})
