# Blast Radius selector

Picks the tests a patch to [sqlglot](https://github.com/tobymao/sqlglot) needs, and splits
them over at most 8 workers, for the Blast Radius track.

```
/data/commits/<id>.patch  ->  one JSON line on stdout: {"commit": id, "workers": [[...], ...]}
```

## How it decides

Nothing is executed at run time: the image carries a coverage map built from sqlglot at the
pinned commit `eb11aec4be8cb2122afe713a4816662c3f0fb2fa`, and answering a batch is a few
file lookups.

1. **Parse and classify each file of the patch** (`selector/diff.py`, `selector/classify.py`).
   The patch is applied in memory to the shipped checkout. A file whose token stream is
   unchanged is a comment/whitespace change. A change outside a function body is
   module-level. Otherwise the changed lines, and the whole enclosing function (a condition
   change can make an operand evaluate where it could not before), are the touched lines.
2. **Map changes to tests** (`selector/candidates.py`) using `maps/maps.pkl.gz`:
   - line coverage per test (`cov`), gathered with `sys.monitoring`;
   - tests that open a file (`opens`) — fixtures included;
   - every test that runs a file, plus every test whose fixture mentions the file's name
     (`fixtures`), for module-level changes;
   - a test file's own tests, for test edits;
   - the whole suite whenever nothing better is known.
3. **Pack** the selected tests over 1..8 workers, cheapest `max load + 2 s per worker`
   (`selector/pack.py`).

Measured on the 30 sample commits: **180.7 s, 0 misses** (baseline: 1 614.1 s), about 0.6 s
of CPU for the batch.

## Rebuilding the map

```sh
conda create -y -p ~/py313 python=3.13
~/py313/bin/pip install -r requirements.lock
git clone https://github.com/tobymao/sqlglot && git -C sqlglot checkout eb11aec4be8cb2122afe713a4816662c3f0fb2fa
~/py313/bin/python -m tools.build_maps --checkout sqlglot --out maps
```

`tools/covplugin.py` records per test; `tools/inline_mp.py` runs sqlglot's `spawn` pool tasks
in-process so their lines belong to the test that submitted them (the pool round trip, pickle
included, is preserved); `tools/childcov/sitecustomize.py` catches any remaining child process.

## Local score

```sh
python -m tools.eval_local            # sample set: simulated + CPU, scored by kit/score.py
python -m tools.breakdown             # per-commit cost against the recorded truth
python -m tools.ablate                # candidate-rule variants on a labelled set
```

`tools/mutants.py` + `tools/label.py` build and label a private validation corpus
(`validation/`, gitignored — nothing derived from a patch is shipped).

## Layout

| path | role |
| --- | --- |
| `selector/` | the shipped program (stdlib only) |
| `maps/maps.pkl.gz` | the committed map (0.8 MB) |
| `tools/` | map building, local scoring, validation corpus |
| `kit/` | starter kit (scorer, sample commits) |
| `tests/` | unit tests for the diff parser, classifier and packer |
