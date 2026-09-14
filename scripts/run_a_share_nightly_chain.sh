#!/usr/bin/env bash
# Run the A-share nightly pipeline pre-stages: precheck → cache.
# The 21:20 head start leaves enough time for the 22:00 content gate.
# prepare stays in the 22:00 content-generation job so base_commit is pinned
# immediately before content generation.
set -euo pipefail
cd /root/projects/etf-rotation-blog
# Keep the scheduler-facing timeout above the runner's total budget so the
# runner can persist the exact blocked stage before the outer process exits.
exec timeout 4500s python3 scripts/run_a_share_nightly_stage.py --stage precheck-cache --timeout 3900
