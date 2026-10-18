# Local SWE-bench Verified harness (no Docker)

This directory has a local harness for 20 SWE-bench Verified instances.
The harness runs on macOS arm64 with uv venvs. It does not use Docker.
The harness does not run an agent or an LLM.

## Contents

| Path | Description |
|---|---|
| `dataset.jsonl` | `princeton-nlp/SWE-bench_Verified`, test split, 500 rows |
| `instances.txt` | The 20 selected instances: id, repo, version, difficulty |
| `validation.json` | Validation results for the 20 instances (no-change run and gold-patch run) |
| `validation_round1.json` | Validation results for all 28 candidates |
| `swe_common.py` | Shared code: specs, directives, parsers, grade functions |
| `prepare.py` | Makes a workdir and a venv for one instance |
| `run_tests.py` | Test runner for the agent |
| `evaluate.py` | Grades the changes in one workdir |
| `validate.py` | Does the gold check and the no-change check |
| `.venv/` | Harness venv: Python 3.11, `swebench==4.1.0`, `datasets` |
| `mirrors/<owner>__<repo>.git` | Bare mirror clones (`git clone --mirror`) |
| `envs/<owner>__<repo>__<version>/` | One uv venv for each (repo, version) |
| `runs/<instance_id>/<timestamp>/` | Evaluation artifacts |

The harness uses `swebench==4.1.0`. Version 5.x removed `MAP_REPO_VERSION_TO_SPECS` and `get_test_directives`.
The harness imports these items from the package:

- `swebench.harness.constants.MAP_REPO_VERSION_TO_SPECS` and `MAP_REPO_TO_REQS_PATHS`
- `swebench.harness.test_spec.python.get_test_directives`
- `swebench.harness.log_parsers.MAP_REPO_TO_PARSER`
- `swebench.harness.grading.get_eval_tests_report` and `get_resolution_status`
- `swebench.harness.utils.get_modified_files`

## Procedure

Set a shell variable for this directory:

```bash
H=/private/tmp/claude-501/-Users-tazo-Desktop-code-mcp/a1a4e9c7-6159-4d41-aebc-38817012f0e0/scratchpad/swebench
```

### 1. Prepare an instance

```bash
$H/.venv/bin/python $H/prepare.py <instance_id> <workdir> [--depth N] [--force]
```

The script prints one JSON line to stdout. Progress messages go to stderr.
The JSON line has these keys:

- `workdir`, `venv`, `python`, `base_commit`
- `test_cmd`: the command that the agent can use to run the relevant tests
- `raw_test_cmd`: the spec `test_cmd` and the directives, for reference only
- `directives`: test files from `test_patch` (Django: module names)
- `skipped_packages`, `skipped_pre_install`: spec items that the harness did not install

The workdir is a plain git checkout on branch `main`.
The workdir has the history up to `base_commit` and the tags that are ancestors of `base_commit`.
The workdir has no remote and no commits after `base_commit`.
The script applies the spec `pre_install` edits and records them in one commit, "SWE-bench".
The ref `refs/swebench/setup` points to that commit.
The official image also makes this commit.

### 2. Run tests during agent work

```bash
$H/.venv/bin/python $H/run_tests.py <instance_id> <workdir> [test ids or paths ...] [--timeout S]
```

The runner installs the repo from `<workdir>` into the venv again.
Then it runs the spec `test_cmd` with the default directives.
If you give test ids or paths, the runner uses them instead of the default directives.
The exit code is the exit code of the test command.

### 3. Evaluate the agent changes

```bash
$H/.venv/bin/python $H/evaluate.py <instance_id> <workdir> [--patch-file F] [--timeout S]
```

The script does these steps:

1. It makes the model patch from all the changes in `<workdir>` relative to `refs/swebench/setup`. The patch includes tracked, untracked, and committed changes. It uses a temporary git index, so the agent index does not change.
2. It removes from the patch all files that `test_patch` touches. It also removes build and test artifacts (`__pycache__`, `*.egg-info`, `.pytest_cache`, and other artifacts).
3. It makes a new `git worktree` of the setup commit in `runs/<id>/<ts>/tree`.
4. It applies the model patch with `git apply`. If that fails, it uses `git apply --reject`. If that fails, it uses `patch --fuzz=5`.
5. It runs the spec `install` from the worktree.
6. It resets the test files to `base_commit` and applies `test_patch`.
7. It runs the spec `eval_commands`, the spec `test_cmd`, and the directives. The time limit is 15 minutes. At the limit, the script kills the process group.
8. It parses the output with `MAP_REPO_TO_PARSER` and grades the result with the swebench grade functions.
9. It installs the repo from `<workdir>` into the venv again and removes the worktree.

The script does not change `<workdir>`.
It prints one JSON line with these keys: `resolved`, `f2p_passed`, `f2p_total`, `p2p_passed`, `p2p_total`, `patch_size` (bytes), `patch_lines_changed`, `patch_files`, `errors`, `f2p_failed`, `p2p_failed`, `run_dir`.
The run directory keeps `model.patch`, `test.patch`, `eval.sh`, `apply.log`, `test_output.txt`, `report.json`, and `result.json`.
The value of `resolved` is true only when all FAIL_TO_PASS tests pass and all PASS_TO_PASS tests pass (`RESOLVED_FULL`).

### 4. Validate the instances again

```bash
$H/.venv/bin/python $H/validate.py [instance_id ...] [--workers 5] [--out validation.json]
```

Without arguments, the script uses `instances.txt`.
For each instance, the script does two checks:

- (a) An evaluation with no change. The result must be "not resolved", and at least one FAIL_TO_PASS test must fail.
- (b) An evaluation with the gold `patch` applied to the workdir. The result must be "resolved".

Instances that use the same venv run one after the other. Instances with different venvs run in parallel.

## Instances

All 20 instances passed both checks. Results are in `validation.json`.
Each instance takes 13 to 130 seconds for the two evaluations.

| Instance | Repo | Version | Difficulty |
|---|---|---|---|
| sympy__sympy-16766 | sympy/sympy | 1.5 | <15 min fix |
| sympy__sympy-16886 | sympy/sympy | 1.5 | <15 min fix |
| sympy__sympy-24539 | sympy/sympy | 1.12 | <15 min fix |
| sympy__sympy-24562 | sympy/sympy | 1.12 | <15 min fix |
| pytest-dev__pytest-7205 | pytest-dev/pytest | 5.4 | <15 min fix |
| pytest-dev__pytest-7432 | pytest-dev/pytest | 5.4 | <15 min fix |
| pylint-dev__pylint-6903 | pylint-dev/pylint | 2.15 | <15 min fix |
| pylint-dev__pylint-7277 | pylint-dev/pylint | 2.15 | <15 min fix |
| pallets__flask-5014 | pallets/flask | 2.3 | <15 min fix |
| psf__requests-5414 | psf/requests | 2.26 | <15 min fix |
| django__django-16429 | django/django | 4.2 | <15 min fix |
| django__django-16642 | django/django | 5.0 | <15 min fix |
| sympy__sympy-23950 | sympy/sympy | 1.12 | 15 min - 1 hour |
| sympy__sympy-24213 | sympy/sympy | 1.12 | 15 min - 1 hour |
| pytest-dev__pytest-7324 | pytest-dev/pytest | 5.4 | 15 min - 1 hour |
| pytest-dev__pytest-7236 | pytest-dev/pytest | 5.4 | 15 min - 1 hour |
| pylint-dev__pylint-7080 | pylint-dev/pylint | 2.15 | 15 min - 1 hour |
| psf__requests-6028 | psf/requests | 2.27 | 15 min - 1 hour |
| django__django-15814 | django/django | 4.2 | 15 min - 1 hour |
| django__django-16082 | django/django | 4.2 | 15 min - 1 hour |

Totals: 12 easy and 8 medium. By repo: sympy 6, pytest 4, pylint 3, django 4, requests 2, flask 1.

Other candidates in `validation_round1.json`:

- These 6 instances are valid, but they are not in the set of 20: sympy__sympy-17655, sympy__sympy-18763, sympy__sympy-24066, django__django-15863, django__django-17087, django__django-16485. You can use them as replacements.
- sphinx-doc__sphinx-10323 and sphinx-doc__sphinx-10466 failed. The spec test command is `tox --current-env`. `tox-current-env` makes `.tox/py39/bin/python` a symlink to the venv interpreter. That symlink has no `pyvenv.cfg` next to it, so Python starts without the venv site-packages. The result is "No module named pytest". The harness does not support sphinx.

## Disk usage

Measured after validation:

| Item | Size |
|---|---|
| `mirrors/` (7 repos, all refs) | 1.5 GB |
| `envs/` (9 venvs) | 528 MB |
| `.venv/` | 249 MB |
| `runs/` (logs only, worktrees are removed) | 6 MB |
| Total for this directory | 2.3 GB |
| uv-managed CPython 3.9.25 (`~/.local/share/uv/python`) | 52 MB |

The harness uses the global uv cache (`~/.cache/uv`, 28 GB before and after, at GB precision).
`du` counts uv clone-linked files at full size, so the real space on APFS is less than the `envs/` value.
A full-history Django workdir uses approximately 300 MB. Use `--depth N` to make it smaller.
`validate.py` deletes its workdirs after each instance.

## Deviations from the official Docker harness

1. **Platform.** The tests run natively on macOS arm64. The official harness uses Linux x86_64 containers that run as root. Path behavior (`/private/var` symlinks, case-insensitive file system), signals, and locale can be different.
2. **Interpreter.** The harness uses uv-managed CPython (`--managed-python`), with the latest patch release of the spec version (3.9.25, 3.11.15). The official harness uses conda Python.
3. **Conda packages.** The spec `packages` strings (for example `pytest`, `mpmath flake8`) are conda package names. The harness installs them from PyPI with `uv pip`, without pins. The versions are the latest at environment creation, not the versions that conda resolved when the official images were built. Specs that use `environment.yml` are not supported.
4. **requirements.txt.** The harness reads the requirements file from the local mirror at `environment_setup_commit`, not from raw.githubusercontent.com. The filter rules are the same. If the bulk install fails, the harness installs each line separately and records the failed lines in `envs/*/.swebench_env.json`. For the 20 instances, no package failed.
5. **Installer.** The harness uses `uv pip install` in place of `python -m pip install`. Editable installs use `-C editable_mode=compat`, which gives a path `.pth` file, as legacy `setup.py develop` does. The default import-hook editable install breaks astroid module discovery for pylint. If a build backend has no `build_editable` (setuptools<64, for example pylint 2.15), the harness installs again with `--no-build-isolation` and the seeded setuptools of the venv. pip does a `setup.py develop` fallback in this condition. Non-editable installs (requests) use `--reinstall --no-deps` after the first install, so that the harness builds the tree again.
6. **One venv for each (repo, version).** All instances of a (repo, version) use one venv. The repo is installed again from the tree before each test run. A file lock serializes `run_tests.py` and `evaluate.py` on one venv. Do not run `run_tests.py` and `evaluate.py` at the same time for two instances that use the same venv. Each run installs its own tree into the venv. The official harness has one image for each instance.
7. **System-level commands.** The harness skips spec commands that use `apt-get`, `locale-gen`, or `conda`. The harness changes `sed -i` to the BSD form `sed -i ''`. All subprocesses get `LANG=LC_ALL=en_US.UTF-8`, a minimal `PATH`, `PYTHONDONTWRITEBYTECODE=1`, and no user `PYTHONPATH`.
8. **Git history.** The official image clones the full repo and deletes the tags after the base timestamp. This harness fetches only `base_commit` and its ancestors, and only the tags that are ancestors of `base_commit`. With `--depth N`, the workdir has no tags. Then tools such as `setuptools_scm` report a placeholder version.
9. **Model patch.** The official harness gets the patch from the agent scaffold. This harness makes the patch from the workdir. The harness removes every file that `test_patch` touches, and the official harness does not. The final result is the same, because both harnesses reset these files before they apply `test_patch`. The harness also removes the artifact patterns in `swe_common.ARTIFACT_EXCLUDES`.
10. **Log parser.** The test output goes to its own file, `test_output.txt`. The parser reads that file, so the START and END markers are not necessary. `MAP_REPO_TO_PARSER` gets a small `test_spec` object (instance_id, repo, version, FAIL_TO_PASS, PASS_TO_PASS), not a full `TestSpec`. The 6 parsers for these repos do not use other fields.
11. **Network.** The tests run with the host network. Some requests tests access the network. These tests are not in FAIL_TO_PASS or PASS_TO_PASS for the selected instances, but they can change the exit code.
12. **Resources.** The harness has no CPU or memory limits, for example the pylint `nano_cpus` limit.
13. **Excluded repos.** Django 3.0 to 3.2 needs Python 3.6, and uv does not supply Python 3.6. The harness excludes these versions. sphinx is excluded (see the Instances section).
