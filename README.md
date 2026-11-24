# redhanded

> Catch the tests that don't catch the change.

**Does a pull request's new or changed test actually react to the code change?**
redhanded gives a reviewer evidence, per test, by running the PR's tests twice: once on the PR's code and once on the code from before the PR.

> Status: working prototype. Python/pytest only. A diagnostic for reviewers, not a CI gate.

---

## The problem

A PR adds a test and some code. Reviewers usually trust that the new test covers the change. Often it does. Sometimes the test passes on the old code too, fails for an unrelated reason, or never runs the changed lines at all. redhanded measures that instead of assuming it.

## What it does

For each new or modified test function in a PR, redhanded runs pytest on two trees, 3 times each:

- **HEAD** — the PR's code.
- **BASE** — the code before the PR, with the PR's test files copied on top.

It then labels each test by *how* it behaves (assertion failure vs runtime error vs import problem vs passes on both vs flaky), and uses per-test coverage to report whether the test runs any changed source line. A test that passes on both versions is reported as information, not automatically rejected.

Every test run happens inside a bubblewrap sandbox: no network, read-only system, cleared environment, only the exported tree mounted, with a timeout and resource limits. See [Sandbox](#sandbox-observed-limits-not-a-security-guarantee).

**No label means a test covers the bug a PR claims to fix.** Labels describe behavior on these two trees only; a reviewer decides what they mean.

## Quick start

The script is still named `catchcheck.py` (its original working name); the project is redhanded.

Requirements: Linux with `bwrap` (bubblewrap), `git`, Python 3.10+.

```bash
# 1. Build a wheels-only venv with the project's test dependencies
./build_venv.sh ~/venvs/myproject [project test dependencies...]

# 2. Run redhanded against a local clone
python3 catchcheck.py \
  --gitdir /path/to/clone \
  --base BASE_SHA --head HEAD_SHA \
  --venv ~/venvs/myproject \
  --out /tmp/out --label myPR
```

Output: a text summary on stdout, plus `OUT/report.json` with raw pytest logs and junit files.

- `--k N` — reruns per tree for flaky detection (default 3).
- `--only FILE` — restrict to specific test files.
- `--instrument-missing-names` — optional, **off by default**. See below.

### Reproduce the trial

```bash
./reproduce.sh WORKDIR [repo-name]   # clones the public repos, fetches the PR refs, builds venvs, runs
```

### Sanity fixtures

```bash
./mkfixture.sh DIR    # tiny synthetic repo
./mkfixture2.sh DIR   # import-failure edge cases
```

## Example

From the real-PR trial, `dateutil#1583` (`yearday=366` leap-year fix), abridged:

```
dateutil#1583  tests/test_relativedelta.py  test_yearday_is_ordinal_day
  new test, untouched base
  ASSERTION-DIFFERS (2/4 params fail on base, all pass on head)
  changed source files: src/dateutil/relativedelta.py
  coverage linkage: test runs changed lines
```

The full machine-readable output for this and 9 other PRs is in [`results/`](results/).

## Labels

| Label | Meaning |
|---|---|
| `ASSERTION-DIFFERS` | An assertion fails on base and passes on head, and the test runs changed lines. Shows the test reacts to this diff. |
| `ASSERTION-DIFFERS-NO-CHANGED-LINES` | Same, but no changed source line ran in the test's own coverage context (cause may be data, config or import-time code). |
| `BASE-RUNTIME-ERROR` | An exception other than an assertion on base, passing on head. Often new behavior or a new signature. Read the traceback. |
| `NEEDS-NEW-API` | The test file cannot import on base. Says a name is new, not that old behavior was wrong. In the default mode this applies to every test in that file. |
| `PASSES-BOTH-EXERCISES-CHANGE` | Passes on both and runs changed lines: the assertion cannot tell old from new behavior. |
| `PASSES-BOTH-UNRELATED` / `PASSES-BOTH` | Passes on both. Often a legitimate test of unchanged behavior. Informational. |
| `FAILS-ON-HEAD` / `FAILS-ON-BOTH` | Fails with the PR applied, or fails identically on both trees (often environment). |
| `FLAKY` | Outcome differs between reruns on the same tree. |

Reports also show how many parameters of a parametrized test fail on base.

## How it works

- **Test selection.** New or modified tests are found by comparing test-function ASTs between base and head. Changes to shared data, helpers or fixtures outside any changed test function make the whole file run, and tests whose result differs are reported.
- **Failure kinds.** Collection errors, assertion failures and other exceptions are told apart from the junit report.
- **Coverage linkage.** Per-test coverage uses coverage.py contexts, limited to changed source files. Lines that run at import time have no test context, so the report says linkage is unavailable instead of claiming zero coverage. Fixture setup run for a test counts as that test.
- **Flaky detection.** Each tree runs `k` times (default 3).
- **Optional instrumented mode** (`--instrument-missing-names`, off by default). For test files that cannot import on the untouched base, it adds a second pass where names that exist only on head are stubbed so each test can run separately. The result is labelled `[instrumented]` and is a counterfactual, never the base. Files that use `hasattr`/`getattr`/`dir`/`vars`/`inspect`/`importlib`/`importorskip`/`skipif`/`skipTest`/`__dict__`/`try` get `INCONCLUSIVE-INSTRUMENTED` with no verdict, because the stub could steer them. Heuristic only.

## Trial on real public PRs

10 PRs from 4 pure-Python libraries (small single-source-file changes): **19 judged tests → 12 ASSERTION-DIFFERS, 5 BASE-RUNTIME-ERROR, 1 PASSES-BOTH, 1 FAILS-ON-BOTH.** No instrumented results occurred; these all use the untouched base.

| PR | Test | Kind | Label |
|---|---|---|---|
| click#3079 | test_shared_param_prefers_first_default | new | ASSERTION-DIFFERS |
| click#3805 | test_parameter_duplication | new | BASE-RUNTIME-ERROR (2/3 params fail on base) |
| click#3805 | test_sentinel_duplication_preserves_identity | new | BASE-RUNTIME-ERROR (4/6 params fail on base) |
| click#3865 | test_make_default_short_help | modified | ASSERTION-DIFFERS (6/36 params fail on base) |
| click#3884 | test_missing_envvar_sequence | new | ASSERTION-DIFFERS (5/6 params fail on base) |
| click#3884 | test_show_envvar_empty | new | ASSERTION-DIFFERS (4/6 params fail on base) |
| dateutil#1583 | test_yearday_is_ordinal_day | new | ASSERTION-DIFFERS (2/4 params fail on base) |
| dateutil#1667 | test_gettz_local_abbreviation_without_time_zone_da | unattributed-change | FAILS-ON-BOTH |
| dateutil#1667 | test_weirdzone_offsets | unattributed-change | ASSERTION-DIFFERS |
| more-itertools#1342 | test_negative_chunk_size | new | ASSERTION-DIFFERS |
| more-itertools#1342 | test_zero_chunk_size | new | BASE-RUNTIME-ERROR |
| more-itertools#1345 | test_eq_above_maxsize | new | BASE-RUNTIME-ERROR |
| more-itertools#1345 | test_hash_above_maxsize | new | BASE-RUNTIME-ERROR |
| python-tabulate#176 | test_floatfmt_decimal | new | ASSERTION-DIFFERS |
| python-tabulate#410 | test_github | modified | ASSERTION-DIFFERS |
| python-tabulate#410 | test_github_multiline | modified | ASSERTION-DIFFERS |
| python-tabulate#410 | test_github_no_alignment | new | PASSES-BOTH |
| python-tabulate#410 | test_github_with_colalign | new | ASSERTION-DIFFERS |
| python-tabulate#410 | test_simple_headerless_with_sep_line_with_padding_ | modified | ASSERTION-DIFFERS |

What the tests react to (checked against the source and test diffs; not checked against each linked issue):

- **click#3884** — sequence or empty `envvar` in the error hint and `--help`.
- **click#3865** — sentence abbreviations like "vs." in short help.
- **click#3079** — the first default wins when flags share a name.
- **click#3805** — copy, deepcopy and pickle of `Sentinel` members; base raises ValueError.
- **more-itertools#1342** — `chunked_even` n<0 / n=0. The negative case is a message mismatch (base also raises ValueError).
- **more-itertools#1345** — `numeric_range` equality and hash above `sys.maxsize`; base raises OverflowError.
- **dateutil#1583** — `relativedelta(yearday=366)` in leap years.
- **dateutil#1667** — the changed test is a test-case generator; whole-file mode found `test_weirdzone_offsets`. The FAILS-ON-BOTH test is probably a sandbox artifact (no /etc, so no system tz data); not investigated.
- **python-tabulate#410** — "github" format becomes an alias of "pipe". `test_github_no_alignment` passes on both and its changed line only runs at import time.
- **python-tabulate#176** — `Decimal` formatting keeps precision.

### Pinned refs

Base and head SHAs for every PR are in [`results/pr_refs.txt`](results/pr_refs.txt), with how each base was derived (`merge` = merge-base of the merge commit's first parent and the PR head; `mb` = merge-base of PR head and default branch; `parent1` = PR head's parent, diff checked to be only source and test files). PR heads are `refs/pull/N/head` on the upstream repos. Raw per-PR reports: `results/*.report.json`.

Four candidates were dropped because the derived base commit pulled in unrelated files: more-itertools#1311 and #1211, humanize#113 and #121.

## Comparison with failfirst

[failfirst](https://github.com/BenMalaga/failfirst) (v0.1.0, commit `14e02b9`, MIT) runs the same basic check for JavaScript (node:test, vitest, jest): run a PR's new tests on the old code and flag the ones that pass. The base idea is not new here, and no failfirst code is used — redhanded is an independent Python implementation. Its source was read at that commit (`src/verdict.js`, `src/runners.js`, `src/cli.js`, `src/detect.js`, README).

**Executed** (real failfirst on a small two-commit JS fixture, see `results/failfirst_js_fixture_run.txt`): one new import crashed the whole test file on base, so every test in it — including one that never touched new behavior — was labelled GOOD. Tests already present by name were PRE-EXISTING and not judged, including an edited one.

**Simulated, not executed** (failfirst cannot run Python): applying its verdict rules to this trial's measured results, 12 of 19 tests get the same answer. The 7 that differ are modified or data-driven tests the by-name rule does not judge (5), a legitimate pass-on-both test it would fail as VACUOUS (1), and an environment failure (1). `ffrule.py` reproduces that table.

Takeaway: on these PRs the main gain is more detail (assertion vs error, changed-line linkage, informational pass-both), not a different answer on most tests. The per-test handling of an import crash is shown only on fixtures.

## Limitations

- Python/pytest only. No GitHub Action wrapper. Test dependencies come from a venv you build per project; PRs that change dependencies, build steps or compiled code are out of scope.
- The tool shows that a test reacts to a diff. Whether it covers a reported bug, or covers behavior that matters, needs a reviewer.
- Coverage is not causality. Import-time lines, fixtures, reflection and conditionally-run code can mislead it.
- Flaky detection is 3 reruns by default; rarer flakiness is missed.
- Only new or modified test functions are judged. Tests selected by node id run without project `addopts` (`-o addopts=`), so projects that depend on them may behave differently.
- Not tested on large repositories or repositories with heavy native dependencies; no timing or cost measurements beyond small runs.

## Sandbox: observed limits, not a security guarantee

Test runs use bubblewrap with `--unshare-all` (user, pid, ipc, uts, net, cgroup namespaces), a cleared environment, read-only /usr and venv and plugin directory, a writable exported copy of the tree, a writable output directory, a tmpfs /tmp, and a timeout plus ulimits for memory, CPU time and file size. Run `python3 audit.py` to probe the isolation from inside the sandbox on your own machine and see what it can and cannot reach (home directory, /etc, network, writable paths, resource limits). Results depend on your host; in development trials it showed no network, no home directory, no /etc and only a handful of environment variables.

This is **not** safe-for-production CI isolation. Known gaps:

- Test code can write the output directory, so a hostile test could forge its own results.
- No process-count limit and no disk quota beyond per-file size.
- No seccomp filter; the kernel is shared. It does not defend against a kernel or bubblewrap escape.
- Dependencies are installed with pip `--only-binary=:all:` outside the sandbox. Wheels run no code at install, but run it at import, which happens only inside the sandbox.
- git operations (`archive`, `diff`, `show`) run on cloned repositories outside the sandbox.

**Do not run it on code you would not otherwise run in a throwaway VM.**

## Provenance and attribution

- Original code in this repository. The red/green idea (run new tests on old code) predates it; see failfirst above and fail-to-pass validation in [SWE-bench](https://github.com/princeton-nlp/SWE-bench).
- The Python libraries and test suites used in the trial belong to their authors under their own licenses; only their public commits were read and run.
- Runtime dependencies: pytest, pytest-cov, coverage (installed from PyPI by `build_venv.sh`).
- No license is attached yet; this is a private working copy.
