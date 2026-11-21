# Blast Radius Test Selector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (or superpowers:subagent-driven-development) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Docker image that, for every `/data/commits/<id>.patch` against sqlglot `eb11aec4be8cb2122afe713a4816662c3f0fb2fa`, prints `{"commit": id, "workers": [[...], ...]}` selecting every failing test at the lowest simulated CI cost (baseline to beat: 1614.1 s on 30 sample commits).

**Architecture:** Two stages per commit. (1) **Static candidates**: parse the diff, map changed lines/files to tests using per-test line coverage and per-test file-open maps built once from the pinned commit and committed to the repo. (2) **Dynamic verification**: apply the patch into a `/tmp` symlink overlay of sqlglot, run the cheap candidates with pytest in a child process, keep only the ones that fail; expensive candidates are selected without running. Output packs the selected tests into 1–8 workers minimizing `max_load + 2·k`.

**Tech Stack:** Python 3.13 (image `python:3.13-slim@sha256:e48f59cd…`), pytest 9.2.0 + pytest-timeout from `kit/requirements.lock`, `sys.monitoring` for coverage, stdlib only in the selector.

**Spec:** `SCORING.md` (repo root) + `kit/README.md` + the task statement (rules summarized below).

## Global Constraints

- Every patch is against sqlglot commit `eb11aec4be8cb2122afe713a4816662c3f0fb2fa`; each patch stands alone.
- Inputs: `/data/commits/<id>.patch`, `/data/tests.json` (`{"tests":[{number,id,file,seconds}], "skipped":[ids]}`). Selector must accept `DATA_DIR` env override (default `/data`) for local runs.
- stdout: exactly one JSON line per commit, nothing else, < 2 MB. All logs → stderr.
- ≤ 8 non-empty workers, no duplicate test numbers per line, only numbers from tests.json.
- Cost per commit: `max worker ms + 2000 ms × workers`; `[]` costs 0; any missed failing test +300 s; container CPU seconds (user+sys, all processes) added.
- "Fails" = pytest failure, error, timeout, import error of the file, or any failing subtest (pytest 9 prints `PASSED` next to `SUBFAILED` — that test failed).
- Labels were produced with `SKIP_INTEGRATION=0` (executor tests run).
- Run limits: no network, 2 cores, 4 GB, **180 s wall for the whole batch** (final: 270 s, ~1.5× commits), user `nobody`, `HOME=/tmp`, read-only FS, **64 MB `/tmp`**, ≤ 256 processes.
- Build limits: network, 4 cores, 8 GB, 10 min. Downloads only from PyPI / Docker Hub / GitHub releases at fixed tag / sqlglot at the pinned commit.
- Repo ≤ 20 MB; no submodules/LFS. Files read at runtime must be world-readable.
- Forbidden: any shipped table from patch / kind of edit → failing tests. Allowed: maps built from the pinned commit (coverage, imports, fixtures). Locally generated mutant labels are for validation only and **must not be committed or shipped** (`.gitignore` them).
- A comment-only change is **not** guaranteed harmless (sample `986c`, `bvw2`, `rszg` mix comments with real breaks; comments inside strings/fixtures change behavior).

## Known facts (verified 2026-11-22)

- Local machine: Windows + WSL Ubuntu (`wsl -d Ubuntu`), conda at `~/miniforge3`, Python 3.14 default. Docker Desktop 29.9.3 installed with WSL integration for Ubuntu enabled; `docker` and `make` work inside WSL (start Docker Desktop first if `docker info` fails). Verified: `wsl -d Ubuntu -- bash -lc "cd /mnt/c/Users/malvi/Desktop/Buildathon/kit && make sample"` builds the starter and scores `Simulated: 1210.470 s, Misses: 0` (CPU not measured locally). sqlglot clone at `~/blast/sqlglot` (WSL), checked out at the pinned commit. sqlglot/ = 3.5 MB, 184 .py files; tests/ = 8.8 MB.
- Repo: git root `C:/Users/malvi/Desktop/Buildathon`, branch `main`, public remote https://github.com/SuperSniper341/blast-radius (`gh` CLI logged in as SuperSniper341). `.gitattributes` forces LF (patches/maps must be byte-exact).
- Kit unpacked at `C:/Users/malvi/Desktop/Buildathon/kit/` (Dockerfile, Makefile, score.py, selector.py, requirements.lock, sample/{commits,answers.json,tests.json}).
- tests.json: 1620 tests, 103.0 s total. Heavy tests: #1490 `test_parser::test_max_nodes` 28.8 s, #1106 `test_executor::test_execute_tpcds` 21.5 s, #1224 `test_generator::test_generator_memory` 6.7 s, #1107 tpch 3.9 s, #1123 optimized_tpch 3.5 s, then optimizer fixture tests 1–2.5 s.
- Sample: 30 commits; kinds `code`/`fixture`/`comment`/`multi`; 12 break nothing; most break 1 test; `2jrq` and `3utz` break tpcds. Lower bound for the sample ≈ 100 s.
- Fixtures are read via `tests/helpers.py` `load_sql_fixtures` / `load_sql_fixture_pairs` with `open(os.path.join(FIXTURES_DIR, filename))`; FIXTURES_DIR is relative to `helpers.py` `__file__` → a symlink overlay keeps paths consistent.
- `sqlglot-integration-tests/` is empty; `sqlglotc/` holds only packaging files.

## Repository layout (repo root = `C:/Users/malvi/Desktop/Buildathon`)

| Path | Responsibility |
| --- | --- |
| `Dockerfile` | Pinned base image, download sqlglot tarball at pinned commit, `pip install -r requirements.lock`, `compileall`, copy selector + maps, `chmod -R a+rX` |
| `requirements.lock` | Copied verbatim from `kit/requirements.lock` |
| `selector/main.py` | Entry: load tests.json + maps, loop commits, schedule work, enforce time budget, print JSON |
| `selector/diff.py` | Parse unified diff; apply it to file contents (no `patch`/`git` in slim image) |
| `selector/classify.py` | Per changed file: kind (`py_src`, `py_test`, `fixture`, `conftest`, `other`), token-level change detection, changed old-line set, function-body vs module-level |
| `selector/candidates.py` | Load maps, turn classified changes into a candidate test-id set |
| `selector/overlay.py` | Build `/tmp/ov_<id>/` symlink farm with patched files written as real files; cleanup |
| `selector/runner.py` | Child-process pytest run of node ids with outcome-collecting plugin; returns `{test_id: "pass"|"fail"}` |
| `selector/pack.py` | `pack(durations_ms: dict[int,int]) -> list[list[int]]` choosing k∈1..8 |
| `maps/coverage.json.gz` | Per source file: line → index into per-file list of test-id bitsets |
| `maps/opens.json.gz` | Non-.py file path (fixtures etc.) and sqlglot .py source files opened via `open()` → tests that opened them; plus `module_users`: module path → tests that executed any line in it |
| `tools/build_maps.py`, `tools/covplugin.py` | Generate maps by running the full suite once under the plugin (local, Python 3.13 env) |
| `tools/mutants.py` | Generate validation patches (local only, output gitignored) |
| `tools/label.py` | Label validation patches by running the full suite on each (local only, gitignored) |
| `tools/eval_local.py` | Run selector against a data dir, measure CPU, score via `kit/score.py` |
| `tests/` | Unit tests for diff/apply, classify, pack |
| `kit/` | Starter kit (score.py, sample) for local evaluation |

---

### Task 0: Local environment matching the scorer image

**Files:** Create `requirements.lock` (copy of `kit/requirements.lock`), `.gitignore` (`/work/`, `/validation/`, `__pycache__/`, `output.jsonl`).

- [ ] **Step 1:** In WSL: `conda create -y -p ~/blast/py313 python=3.13` then `~/blast/py313/bin/pip install -r /mnt/c/Users/malvi/Desktop/Buildathon/requirements.lock`.
- [ ] **Step 2:** Baseline the suite: `cd ~/blast/sqlglot && SKIP_INTEGRATION=0 ~/blast/py313/bin/python -m pytest -q -p no:cacheprovider tests` → expect all pass except the 1 skipped id listed in tests.json `skipped`. Record any baseline failures (they would be labeled failing on every commit; there should be none).
- [ ] **Step 3:** Docker: start Docker Desktop, confirm `docker version` works (Windows shell) — and inside WSL after enabling WSL integration. The kit Makefile needs `make` (use WSL). Until Docker works, all evaluation goes through `tools/eval_local.py` (Task 7). Repo already initialized and pushed.

### Task 1: Coverage + file-open maps (`tools/covplugin.py`, `tools/build_maps.py`)

**Interfaces — Produces:**
- `maps/coverage.json.gz`: `{"tests": [test_id,...], "files": {"sqlglot/parser.py": {"sets": ["<hex bitset>",...], "lines": {"123": set_index,...}}, ...}}`. Bit *i* = `tests[i]`. Covers every `.py` under `sqlglot/` and `tests/` (test modules, `helpers.py`, `conftest.py` if present).
- `maps/opens.json.gz`: `{"opens": {"tests/fixtures/optimizer/simplify.sql": "<hex bitset>", ...}, "module_users": {"sqlglot/dialects/snowflake.py": "<hex bitset>", ...}}`. Same `tests` index order as coverage.

- [ ] **Step 1:** `covplugin.py` = pytest plugin. In `pytest_runtest_protocol` (hookwrapper) per test: `sys.monitoring.use_tool_id(sys.monitoring.COVERAGE_ID, ...)`, register a `LINE` callback that adds `(code.co_filename, line)` to the current test's set and returns `sys.monitoring.DISABLE`; call `sys.monitoring.restart_events()` at the start of each test so every test sees every line once. Wrap `builtins.open` and `io.open` to record paths under the repo root (relativized). Include setup/teardown (fixtures, `setUpClass`) in the test's attribution — for class-level setup, attribute lines executed during class setup to **every test in that class** (record setup lines in the first test, then union across the class when writing maps).
- [ ] **Step 2:** Import-time lines: lines executed while a module is first imported are only seen by whichever test triggered the import. Do **not** rely on line coverage for them; `module_users[path]` = union of tests that executed any line in that module, OR'd with all tests in any test file whose collection imported it (capture `sys.modules` keys after collection, before the first test, per test file → those modules count as used by every test of that file).
- [ ] **Step 3:** `build_maps.py` runs `SKIP_INTEGRATION=0 python -m pytest -p tools.covplugin -p no:cacheprovider tests` inside the sqlglot checkout (serial; xdist would break attribution), then writes both maps with `json.dumps(..., separators=(",",":"))` + gzip level 9. Test ids must equal tests.json ids byte-for-byte (`tests/x.py::Class::test`).
- [ ] **Step 4:** Verify: every id in `kit/sample/tests.json` appears in `maps` `tests`; map files together < 15 MB; spot check that `sqlglot/optimizer/eliminate_joins.py` line 71 bitset contains `tests/test_executor.py::TestExecutor::test_execute_tpcds` and that `opens["tests/fixtures/optimizer/simplify.sql"]` contains `tests/test_optimizer.py::TestOptimizer::test_simplify`.
- [ ] **Step 5:** Commit maps + tools.

### Task 2: Diff parsing and application (`selector/diff.py`)

**Interfaces — Produces:**
- `@dataclass FilePatch: old_path: str|None; new_path: str|None; hunks: list[Hunk]; is_binary: bool`
- `@dataclass Hunk: old_start: int; old_len: int; new_start: int; new_len: int; lines: list[str]` (each line keeps its `' '`/`'-'`/`'+'` prefix; `\ No newline at end of file` handled)
- `parse_patch(text: str) -> list[FilePatch]` — handles `/dev/null` (new/deleted files), `rename from/to`, mode-only changes, `Binary files differ` (`is_binary=True`).
- `apply(original: str|None, fp: FilePatch) -> str|None` — exact-context application; returns `None` for deletion; raises `PatchError` on context mismatch.

- [ ] **Step 1:** Tests in `tests/test_diff.py`: every `kit/sample/commits/*.patch` parses and applies cleanly to the pinned checkout (read sources from `SQLGLOT_DIR` env, default `~/blast/sqlglot`); a synthetic new-file patch and delete-file patch round-trip; a patch with mismatched context raises `PatchError`.
- [ ] **Step 2:** Implement; run `pytest tests/test_diff.py` → PASS; commit.

### Task 3: Change classification (`selector/classify.py`)

**Interfaces — Produces:**
- `@dataclass Change: path: str; kind: Literal["py_src","py_test","conftest","fixture","other"]; token_changed: bool; changed_lines: set[int]  # old-file line numbers; module_level: bool; deleted_or_new: bool`
- `classify(fp: FilePatch, old_text: str|None, new_text: str|None) -> Change`

Rules (decisions the implementer cannot infer):
- `kind`: `sqlglot/**.py` → `py_src`; `tests/**/conftest.py` → `conftest`; `tests/**.py` → `py_test`; anything under `tests/fixtures/` → `fixture`; everything else → `other`.
- `token_changed` for `.py`: compare `tokenize` streams of old vs new with `COMMENT`, `NL`, `NEWLINE`, `INDENT`, `DEDENT` *types kept but comment text dropped*… precisely: drop `COMMENT` and `NL` tokens, compare `(type, string)` sequences. A `#` inside a string is part of a `STRING` token, so string edits are caught. If tokenize fails on new text → `token_changed=True, module_level=True` (syntax error = import error).
- `changed_lines`: old line numbers of every `-` line; for pure insertions, the old lines immediately before and after the insertion point.
- `module_level`: True if any changed line is not strictly inside the body of a `FunctionDef`/`AsyncFunctionDef`/`Lambda` in the **old** AST (decorators, default args, signatures, class bodies, module statements all count as module-level). Also True if the new AST's set of top-level/class-level names differs.
- Non-`.py` files: `token_changed=True` (always treated as a real change).

- [ ] **Step 1:** Tests in `tests/test_classify.py`, using sample patches: `9u5k`, `bap8`, `f4xa` → `token_changed=False`; `2haz` (`is`→`==` inside method) → `token_changed=True, module_level=False, changed_lines={1280}`; `4d63` (`NUMERIC_ESCAPES` class attr) and `535z` (`WEEK_OFFSET`) → `module_level=True`; `2vdj` → `kind="fixture"`; inserting `"# x"` inside a triple-quoted string → `token_changed=True`.
- [ ] **Step 2:** Implement; tests PASS; commit.

### Task 4: Candidate selection (`selector/candidates.py`)

**Interfaces:**
- Consumes: maps from Task 1, `Change` from Task 3.
- Produces: `class Maps: load(dir: Path) -> Maps`; `candidates(changes: list[Change], maps: Maps, all_ids: list[str]) -> set[str] | ALL` where `ALL` is a sentinel meaning "every test".

Rules:
- `py_src`, `token_changed=False`: candidates = `opens[path]` (tests that read the source text) — usually empty.
- `py_src`, function-body change: union of coverage bitsets of `changed_lines`; for inserted lines use the surrounding lines' sets; ∪ `opens[path]`.
- `py_src`, `module_level=True` or new/deleted file: `module_users[path]`; if path is `sqlglot/__init__.py`, `sqlglot/expressions/**`, `sqlglot/tokens.py`, `sqlglot/helper.py`, or the path is absent from `module_users` → `ALL`.
- `py_test`: all tests whose id starts with `path + "::"`; module-level change in `tests/helpers.py` → every test whose file imports helpers (use `module_users["tests/helpers.py"]`); function-body change in helpers → coverage sets.
- `conftest` / `other` files referenced in `opens` → `opens[path]`; `conftest` → `ALL`; `other` not in `opens` (README, docs, CHANGELOG, posts/, benchmarks/) → empty set. `setup.py`/`pyproject.toml`/`setup.cfg`/`Makefile` → `opens[path]` ∪ tests in `tests/test_sqlglotc.py`, `tests/test_build.py`, `tests/test_dialect_entry_points.py`.
- `fixture`: `opens[path]`; a fixture path not in `opens` → all tests of every test file whose source text contains the fixture's basename (grep the committed copy of test files at runtime from `/app/sqlglot/tests`), else `ALL`.
- Map ids → numbers via tests.json; ids in tests.json absent from the map are always candidates.

- [ ] **Step 1:** Tests in `tests/test_candidates.py`: for every sample commit, `answers.failing ⊆ candidates` (load `kit/sample/answers.json`). This is the no-miss invariant; it must hold before Task 6 adds runtime pruning.
- [ ] **Step 2:** Implement; PASS; print per-commit candidate count and seconds to stderr for inspection; commit.

### Task 5: Overlay + runner (`selector/overlay.py`, `selector/runner.py`)

**Interfaces — Produces:**
- `build_overlay(root: Path, patched: dict[str, str|None], dest: Path) -> Path` — `root` is the read-only sqlglot checkout (`/app/sqlglot`). Recreate only the directories on the path to each patched file as real dirs; symlink every other entry at the highest possible level (`os.symlink`). Write patched files as real files; deletions simply omit the symlink. Real dirs that contain a patched `.py` must not symlink `__pycache__` wholesale: create a real `__pycache__` with per-file symlinks to every pyc except the patched module's.
- `run_tests(overlay: Path, test_ids: list[str], timeout_s: float) -> dict[str, bool]` — True = failed. Runs `python -m pytest -q -p no:cacheprovider -p selector.outcomes --timeout=<per-test> -o addopts= <ids>` with `cwd=overlay`, env `PYTHONPATH=<overlay>:/app`, `SKIP_INTEGRATION=0`, `PYTHONDONTWRITEBYTECODE=1`, `HOME=/tmp`. Plugin `selector/outcomes.py` writes `{id: failed}` JSON to a file in `/tmp`; a test is failed if any phase (setup/call/teardown) fails/errors, any subtest report fails, or it is in a module whose collection errored. Per-test timeout = `max(5, 4 × recorded seconds)`; timeout counts as failed. If the child dies, overruns the commit's wall budget, or any requested id is missing from the results, return failed=True for every un-reported id.
- Delete the overlay dir after each commit (64 MB `/tmp`).

- [ ] **Step 1:** Tests in `tests/test_runner.py` (local, needs `~/blast/sqlglot` and py313): overlay for sample `bj7g` + run `tests/dialects/test_clickhouse.py` ids → exactly the answers' failing id(s) report failed; overlay for `9u5k` → none fail; a synthetic patch adding `raise ImportError` at top of `sqlglot/dialects/drill.py` → all drill tests failed; a patch making a test `while True: pass` → reported failed via timeout.
- [ ] **Step 2:** Implement; PASS; measure and record child startup CPU (pytest import + sqlglot import + collection) in plan notes; commit.

### Task 6: Run-vs-select policy, packing, main loop (`selector/pack.py`, `selector/main.py`)

**Interfaces — Produces:** `pack(durations_ms: dict[int, int]) -> list[list[int]]`: for k in 1..8 do LPT (longest first onto least-loaded worker), cost = `max_load + 2000·k`, return the cheapest (ties → fewer workers); empty input → `[]`.

Policy per commit (all costs in seconds):
1. Candidates `C` (Task 4). If `C` is empty → `[]`, no run. If `C` is `ALL` → candidates = every test.
2. Heavy set `H` = tests in `C` with recorded `seconds ≥ HEAVY` (start `HEAVY = 3.0`; tune in Task 7). Run set `R = C − H`.
3. Skip running if `sum(R seconds) + STARTUP` ≥ `cost(pack(C)) − cost(pack(H))` (running can't pay for itself) → output `pack(C)`.
4. Else run `R` in the overlay; output `pack(H ∪ failed(R))`.
5. Budget: process commits on a `ThreadPoolExecutor(max_workers=2)` (each run is a child process, so 2 cores are used). Global deadline `DEADLINE = 0.75 × 180 s` measured from start; commits not yet run when the deadline passes output `pack(C)`. Order commits by estimated benefit descending so cheap wins happen first. Final run (270 s, 1.5× commits) uses the same per-commit budget: derive deadline from `len(commits)` as `min(0.75×180, …)` — simplest: `DEADLINE = 4.5 s × n_commits` capped at 150 s.
6. Any exception in a commit's pipeline → output `pack(C)` (or all tests if `C` unknown). Never crash; always print every commit. Use `print(..., flush=True)` only from the main thread.

- [ ] **Step 1:** Tests `tests/test_pack.py`: `pack({})==[]`; single 28817 ms test → one worker; `{1:21454,2:3863,3:3537,4:2533}` → cost equals hand-computed optimum; result never has > 8 workers or duplicates.
- [ ] **Step 2:** Implement pack + main; PASS; commit.

### Task 7: Local evaluation + validation corpus (`tools/eval_local.py`, `tools/mutants.py`, `tools/label.py`)

- [ ] **Step 1:** `eval_local.py --data kit/sample --answers kit/sample/answers.json`: runs `python -m selector.main` with `DATA_DIR`, `SQLGLOT_DIR=~/blast/sqlglot`, measures child CPU via `resource.getrusage(RUSAGE_CHILDREN)` and wall time, writes `output.jsonl`, calls `kit/score.py`, prints `simulated`, `cpu`, `total`, `misses`, wall time. Run under `taskset -c 0,1` to mimic 2 cores.
- [ ] **Step 2:** `mutants.py --n 200 --out validation/`: deterministic (seeded) mutants of the pinned checkout mirroring the sample kinds plus unseen kinds: operator flips (`and/or`, `==/!=`, `is/==`, `</<=`), constant changes, boolean flips, comment insertions in .py (including inside docstrings/strings), fixture line edits, test-file edits, multi-file combos, new file, deleted line, whitespace-only changes. Emit unified diffs (`difflib.unified_diff` with `a/` `b/` prefixes and git-style headers). `label.py` applies each via the overlay and runs the **full** suite with pytest (`-n 4` xdist allowed for labeling only), writing `validation/answers.json` in the kit format. Both are gitignored — never shipped.
- [ ] **Step 3:** Gate: `misses == 0` on sample and on validation corpus. Then tune `HEAVY`, `DEADLINE`, threading using validation totals. Record best numbers in this plan's notes section.
- [ ] **Step 4:** Commit tools (not data).

### Task 8: Dockerfile + submission

**Files:** `Dockerfile`, `README.md` (short: approach, how to rebuild maps).

- [ ] **Step 1:** Dockerfile:
  - `FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90`
  - `ADD https://github.com/tobymao/sqlglot/archive/eb11aec4be8cb2122afe713a4816662c3f0fb2fa.tar.gz /tmp/s.tgz` → extract to `/app/sqlglot` (strip top dir).
  - `pip install --no-cache-dir -r requirements.lock`
  - `python -m compileall -q /app/sqlglot/sqlglot /app/sqlglot/tests /app/selector`
  - `COPY selector/ maps/` ; `chmod -R a+rX /app`
  - `ENV SKIP_INTEGRATION=0 PYTHONDONTWRITEBYTECODE=1 SQLGLOT_DIR=/app/sqlglot`
  - `CMD ["python", "-m", "selector.main"]` with `WORKDIR /app`.
- [ ] **Step 2:** If Docker is available: `make -C kit sample IMAGE=...` style run using kit's `RUN_FLAGS` against the repo Dockerfile → `misses 0`, total well below 1614.1. Verify the build stays < 10 min (should be ~1 min).
- [ ] **Step 3:** `git push` to https://github.com/SuperSniper341/blast-radius (public); submit repo URL + commit hash.

## Review Focus

1. **Import-time / class-body edits** (e.g. `WEEK_OFFSET`, `NUMERIC_ESCAPES`, base-class attributes in `tokens.py`/`generator.py`) — must widen to `module_users`/`ALL`, never to line coverage. Test: `4d63`, `535z` candidates ⊇ failing (Task 4).
2. **Comment/whitespace that is not harmless** — `#` inside strings/docstrings, `# dialect:` / `# title:` / `# execute:` directives in fixtures, comments in SQL fixtures. Test in Task 3 (string comment) + Task 7 mutants.
3. **Runner fidelity vs pytest labels** — subtests, setup errors, collection/import errors, timeouts, tests relying on order/state. Task 5 tests cover import error and timeout; validation corpus checks the rest.
4. **Unseen change kinds** — new/deleted/renamed files, binary, test-file edits, conftest, packaging files. Fallback must be conservative (`ALL` or `pack(C)`), never empty. Covered by Task 7 mutants.
5. **Resource limits** — 64 MB `/tmp` (delete overlays), 256 pids (≤ 2 concurrent children, no xdist at runtime), read-only FS (no pyc writes), 180 s wall (deadline fallback). Check with `eval_local.py` wall-time output and the Docker run.

## Notes (decisions taken while executing)

- **No tests run at run time.** Running a candidate costs about its recorded seconds of CPU,
  while selecting it costs at most that (spread over workers) and often just the 2 s worker
  fee. Arithmetic on the sample: for a 1-test candidate set, running costs 0.02 + ~0.3 s
  startup = 0.32 s and saves 2.01 s only when the test passes; with 40% of commits breaking
  nothing the two expectations nearly cancel, and for every wider set running loses outright
  (sum of candidates >> max load). The image therefore ships a precomputed map and no pytest.
- **Map building runs `tests/inline_mp`**: sqlglot's optimizer/executor tests submit work to
  `spawn` workers; `sys.monitoring` in the parent cannot see those lines, so the map run
  replaces `ProcessPoolExecutor` with an inline executor. This is a map-building-only patch:
  it changes no shipped behaviour.
- **Fixtures**: `maps.fixtures` maps a module stem (`tsql`, `simplify`, ...) to the tests that
  open a fixture mentioning it, which is what catches a dialect flag change breaking a
  fixture-driven optimizer test.
- Best local totals: sample **184.8 s simulated + ~0.9 s CPU, 0 misses** (baseline 1 614.1 s).
  Journey: 1 210 s (run everything) → 1 749 s / 5 misses (first map) → 516 s / 1 miss (spawn
  workers traced) → 217 s / 0 misses (span widening) → 180.7 s (fixture map, no names map) →
  184.8 s (dialect family, docstring rule) — the last step trades 4 s for the `risingwave`
  class-attribute miss the validation corpus found.

## Review findings (fixed after the first working version)

1. `mode_only` never ran: the "no hunks" guard came first. Reordered.
2. A patch that no longer compiles is now detected with `compile()` and widens to
   `rev_users` (every module that imports the file), because an import error fails all of them.
3. Deletions and renames widen the same way instead of returning the whole suite.
4. `setUpClass`/`tearDownClass` lines ran inside one test only: the plugin now records
   setup/teardown-only lines per test and the map builder spreads them over the class.
5. Collection-time code in test modules was attributed to nobody: recorded per test module and
   added to every test of that file, keeping only lines inside a function body.
6. Test files import each other (`tests.dialects.test_dialect.Validator`, 33 files): the import
   map now follows `tests.*` too, and a test module's module-level change widens to importers.
7. Lambda header lines counted as function bodies, so editing a dict key that happens to hold a
   lambda looked like a body edit; the header is now a body line only when its prefix is unchanged.
8. A function-body change whose span no test executes used to return nothing; it now falls back
   to the file's users.

## Progress log

- Maps: `maps/maps.pkl.gz` (0.8 MB, 1621 ids incl. the skipped one, all 1620 tests.json ids present).
- Unit tests: `tests/test_diff.py`, `tests/test_classify.py`, `tests/test_pack.py` pass.
- Local harness: `python -m tools.eval_local` (CPU from `getrusage(CHILDREN)`, score from `kit/score.py`).
- Validation corpus: `python -m tools.mutants --n 96`, labelled by `python -m tools.label`
  under `validation/` (gitignored). Labelling a mutant costs ~1-5 min (full suite), so the corpus
  is labelled in priority batches: `--per-kind 5` stratifies across the seven mutant kinds.
