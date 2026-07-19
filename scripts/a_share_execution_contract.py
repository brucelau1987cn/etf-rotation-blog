#!/usr/bin/env python3
"""Shared A-share execution-state semantics for audit and validation."""

EXECUTION_ELIGIBLE_STATES = frozenset({"可持有", "回踩候选"})
ALL_TRADE_STATES = frozenset({"可持有", "回踩候选", "观察", "退出"})
