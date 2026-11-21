# sqlglot test selector

Picks the pytest tests a patch to [sqlglot](https://github.com/tobymao/sqlglot) needs, and
splits them over at most 8 workers.

```
/data/commits/<id>.patch  ->  one JSON line on stdout: {"commit": id, "workers": [[...], ...]}
```

A commit costs the recorded seconds of its busiest worker plus 2 s per worker; leaving out a
test that fails on it costs far more, so every rule below errs towards over-selection.

## How it decides

Nothing is executed at run time: the tree carries a map built from sqlglot at commit
`eb11aec4be8cb2122afe713a4816662c3f0fb2fa`, and answering a batch is a few file lookups.

1. **Parse and classify each file of the patch** (`selector/diff.py`, `selector/classify.py`).
   The patch is applied in memory to the shipped checkout. A file whose token stream is
   unchanged is a comment/whitespace edit. A change outside a function body is module-level.
   Otherwise the touched lines are the changed lines plus the whole enclosing function (a
   condition change can make an operand evaluate where it could not before).
2. **Map changes to tests** (`selector/candidates.py`) using `maps/maps.pkl.gz`:
   - line coverage per test (`cov`), recorded with `sys.monitoring`;
   - tests that open a file (`opens`) — query fixtures included;
   - for module-level edits, every test that runs the file, every test whose fixture names
     the file, the users of modules that import it (`rev_users`, `imports`), and the whole
     dialect test family — module-level data is read by code in other files, so line coverage
     alone cannot see it;
   - for a file that stops importing (syntax error, deletion, rename), the whole suite:
     sqlglot loads dialects by name at run time, so which tests import it is not decidable
     from a map;
   - a test file's own tests, for test edits;
   - nothing at all for a new module or an edit outside `sqlglot/` and `tests/`.
3. **Pack** the selected tests over 1..8 workers at the cheapest `max load + 2 s per worker`
   (`selector/pack.py`).

On the 30 sample commits: **323.5 s, 0 misses** (selecting everything costs 1 210 s), plus
about 1 s of CPU for the whole batch.

## Rebuilding the map

```sh
conda create -y -p ~/py313 python=3.13
~/py313/bin/pip install -r requirements.lock
git clone https://github.com/tobymao/sqlglot && git -C sqlglot checkout eb11aec4be8cb2122afe713a4816662c3f0fb2fa
~/py313/bin/python -m tools.build_maps --checkout sqlglot --out maps
```

`tools/covplugin.py` records per test (including class fixtures and collection-time code);
`tools/inline_mp.py` runs sqlglot's `spawn` pool tasks in-process so their lines belong to the
test that submitted them, keeping the pickle round trip; `tools/childcov/sitecustomize.py`
catches any remaining child process.

## Checking it

```sh
python -m pytest tests                       # diff parser, classifier, packer, end-to-end guard
python -m tools.eval_local                   # one batch: cost + CPU, scored by sample/score.py
python -m tools.breakdown                    # per-commit cost against the recorded truth
python -m tools.mutants --n 96 --out validation
python -m tools.label --commits validation/commits --out validation/answers.json
python -m tools.validate validation          # miss rate by kind of change
```

`tools/mutants.py` + `tools/label.py` build and label a private corpus of synthetic broken
commits; `validation*/` is gitignored so nothing derived from a patch is shipped.

## Layout

| path | role |
| --- | --- |
| `selector/` | the shipped program (stdlib only) |
| `maps/maps.pkl.gz` | the committed map (0.47 MB) |
| `tools/` | map building, local scoring, validation corpus |
| `sample/` | 30 sample commits, their known failing tests, and the scorer for local checks |
| `tests/` | unit tests for the diff parser, classifier and packer |
