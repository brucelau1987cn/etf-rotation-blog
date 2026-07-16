# Kronos ETF shadow runtime

Kronos runs as a separate research sidecar. It does not feed production scores,
actions, levels, positions, or paper-trading execution.

## Frozen components

- Source: `shiyu-coder/Kronos`
- Source revision: `67b630e67f6a18c9e9be918d9b4337c960db1e9a`
- Model: `NeoQuasar/Kronos-mini`
- Model revision: `f4e68697d9d5aed55cef5c96aabc3376bcad9f81`
- Tokenizer: `NeoQuasar/Kronos-Tokenizer-2k`
- Tokenizer revision: `26966d0035065a0cae0ebad7af8ece35bc1fb51c`
- License at the frozen source/model revisions: MIT

The mini checkpoint is used because the production host is CPU-only with 3.8 GiB
RAM. The output schema keeps model identifiers explicit, so a later small-model
experiment can publish a separate comparable series.

## Runtime layout

```text
/root/.cache/etf-kronos/
├── Kronos/       # frozen source checkout
├── hf/           # Hugging Face model cache
└── venv/         # isolated Python runtime
```

Install CPU PyTorch from its CPU wheel index, then install
`requirements-kronos.txt`. Initial model download is allowed only during an
explicit bootstrap; scheduled inference uses the local cache.

## Generate

```bash
/root/.cache/etf-kronos/venv/bin/python \
  scripts/generate_kronos_shadow.py --batch-size 8
```

Artifact:

```text
public/data/model-lab/a-share-kronos-shadow.json
```

Local history:

```text
data/local/model-lab/a-share-kronos-shadow-history.jsonl
```

## Frozen inference policy

- 89 formal rotation ETFs
- qfq and final daily bars only
- latest 256 bars; minimum 96 for newer ETFs
- OHLC input; volume and amount excluded because long-history provider coverage is mixed
- five real Shanghai/Shenzhen trading sessions from BaoStock calendar
- seed `20260716`
- `T=1.0`, `top_k=1`, `top_p=1.0`, `sample_count=1`
- CPU float32, eval mode, deterministic algorithms
- batch size 8

`top_k=1` creates one deterministic greedy path. The path is a model audit output,
not a confidence interval. Raw OHLC envelope violations are preserved and marked
in `quality.raw_ohlc_valid`.

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

The dashboard build and 21:50 preparation gate both validate the artifact.
