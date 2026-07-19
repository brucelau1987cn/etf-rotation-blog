# ETF sequence-path shadow research

The sequence-path component runs as an isolated research sidecar. It does not feed production scores, actions, levels, positions, or paper-trading execution.

## Reproducibility boundary

Frozen source, model, tokenizer and runtime revisions belong in a private reproducibility ledger. Public pages and static JSON expose only model-agnostic parameters required for display and audit. Scheduled inference uses a pre-provisioned isolated runtime and local finalized data cache.

## Generate

```bash
python3 scripts/generate_kronos_shadow.py --batch-size 8
```

Public artifact:

```text
public/data/model-lab/a-share-path-shadow.json
```

Private append-only history remains under `data/local/` and is excluded from Git and static publication.

## Frozen inference policy

- 89 formal rotation ETFs
- qfq and final daily bars only
- latest 256 bars; minimum 96 for newer ETFs
- OHLC input
- five real Shanghai/Shenzhen trading sessions
- deterministic single-path inference
- isolated CPU runtime

The output is a research path, not a confidence interval. Raw OHLC envelope violations are preserved and marked in `quality.raw_ohlc_valid`.

## Publication gates

The nightly pipeline requires:

- `mode=shadow_research_only`
- both production-change flags set to false
- `production_role=display_and_audit_only`
- final qfq formal-rotation basis
- current trade date
- 89/89 symbol coverage
- five future sessions and five finite OHLC steps per symbol
- exact symbol-set match with the formal rotation universe
- strict recursive public schema with unknown and sensitive keys rejected

The dashboard build and 21:50 preparation gate both validate the artifact. The publisher verifies the exact candidate commit tree and the deployed public-file hashes.
