#!/usr/bin/env python3
"""Which public-contract errors a single-market publisher may tolerate.

Every publisher in this repository shares one worktree, one site build and one
validator, but each owns one market. When another market's pipeline is stalled,
the validator reports *its* drift, and a publisher that demands a globally clean
result fails for a reason it can neither fix nor leak.

2026-10-31: the US weekly research job died at
``validate_public_data_contracts.py`` because an A-share stage had left
``a-compass-dashboard`` stale against its pool. The US job publishes US data
only, commits an explicit file list, and cannot absorb A-share edits — so that
error was pure noise to it.

Keep ONE list here. Copying the allowlist into each publisher is how the copies
drift apart, and a drifted copy fails on the file the other one already
whitelisted.
"""
from __future__ import annotations

# Prefixes naming a dataset that another scheduled publisher owns and
# regenerates. A US-only publisher can neither produce nor commit these.
FOREIGN_ERROR_PREFIXES = (
    "A-share ",
    "a-share-",
    "A research audit ",
    "A action price ",
    "catalog ",
)

# Exact messages that are known foreign drift. The dashboard is a deterministic
# projection of the A-share pool; it disagrees only between an A-share stage and
# that stage's own derive step.
FOREIGN_ERRORS = frozenset({
    "a-compass-dashboard differs from etf-garden-pool export",
})

# Opt-in switches for the validator *inside* the shared `npm run build` chain,
# which a publisher reaches only through the environment. Without them the build
# keeps its strict, fail-closed behaviour.
ALLOW_FOREIGN_ENV = "PUBLIC_CONTRACT_ALLOW_FOREIGN_DRIFT"
OWNED_DATASETS_ENV = "PUBLIC_CONTRACT_OWNED_DATASETS"


def catalog_dataset(message: str) -> str | None:
    """Return the dataset key a `catalog <key> ...` error names, if any."""
    if not message.startswith("catalog "):
        return None
    rest = message[len("catalog "):].split(" ", 1)[0]
    return rest or None


def is_foreign_error(message: str, owned_datasets: frozenset[str] = frozenset()) -> bool:
    """True when the message names a dataset this publisher does not own.

    `catalog <key> ...` is tolerated only for a key the caller does not own: a
    stale catalog entry for its OWN output means the catalog was not regenerated
    before publishing, which is exactly the mistake the gate exists to catch.
    """
    if not isinstance(message, str):
        return False
    if message in FOREIGN_ERRORS:
        return True
    if not message.startswith(FOREIGN_ERROR_PREFIXES):
        return False
    dataset = catalog_dataset(message)
    if dataset is not None and dataset in owned_datasets:
        return False
    return True


def split_errors(
    errors: list[str], owned_datasets: frozenset[str] = frozenset(),
) -> tuple[list[str], list[str]]:
    """Return (own, foreign) errors, preserving order."""
    own = [error for error in errors if not is_foreign_error(error, owned_datasets)]
    foreign = [error for error in errors if is_foreign_error(error, owned_datasets)]
    return own, foreign
