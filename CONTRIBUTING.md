# Contributing

This repository has two very different kinds of content, and they are governed
by different rules. Read the root `README.md` / `README_zh.md` first for the
overall layout; this file only covers process.

## 1. The frozen dataset and protocol (do not edit in place)

Everything under `数据集及参考计划/`, `提示词与接口配置/`, and the pre-existing
scripts in `运行代码与说明/` (`adapters.py`, `protocol.py`,
`observation_render.py`, `generate_schemas.py`, `build_reference_plans.py`,
`verify_release.py`, and `verification_report.json`) is the **frozen**
release, identified by two separate fields in
`提示词与接口配置/release_config.json`: `protocol_version`
(`kaxinguo_feasibility_v1`) and `dataset_id`
(`home_branch_planning_dataset_v3`). They are independent identifiers, not
two names for the same thing — a fix might need to bump one, the other, or
both, depending on what changed.

- If you believe something in this frozen set is *wrong* (a scenario, a
  schema, a prompt, a reference plan), open an issue describing the problem
  and why it's wrong. Do not send a PR that edits the frozen files directly.
- Any accepted fix to frozen content must ship as a **new, internally
  consistent version** — not a single-field edit. In particular,
  `live_runner.py` hard-fails if `release_config.json`'s `protocol_version`
  ever disagrees with the `VERSION` constant hardcoded in `protocol.py`
  (`adapters.py`'s request builder and every schema's `protocol_version`
  `const` make the same assumption). So bumping `protocol_version` means
  cutting a new frozen core: update `protocol.py`'s `VERSION` **and**
  `generate_schemas.py`'s own `VERSION` constant — the two are separate
  hardcoded literals, not one shared import, so updating only `protocol.py`
  and re-running `generate_schemas.py` silently regenerates schemas that
  still embed the old version — then regenerate the schemas
  (`generate_schemas.py`) and reference plans
  (`build_reference_plans.py`) so their embedded `protocol_version` values
  agree, update every data file that embeds the old value, bump
  `release_config.json`, regenerate `运行代码与说明/release_manifest.json`,
  and add a `CHANGELOG.md` entry describing exactly what changed and why.
  A `dataset_id` bump (new/changed scenarios without a protocol change) is
  narrower but still needs the manifest regenerated and a changelog entry.
  Silent in-place edits to a version that's already been referenced
  elsewhere defeat the point of freezing it — reproducibility requires that
  a given `protocol_version`/`dataset_id` pair always means the same bytes.
- PRs that touch anything under `提示词与接口配置/` or `数据集及参考计划/`
  without going through the coordinated bump above will be asked to do so
  before merge.

## 2. The tooling (`live_runner.py`, `mock_server.py`, `summarize_results.py`)

This is ordinary, evolvable code, not frozen data. Bug reports, flag
additions, and refactors are welcome as normal PRs.

- Match the existing style: short module docstrings explaining the *why* or
  the one non-obvious invariant, not a comment on every line. Prefer
  self-explanatory names over inline narration. Don't add docstrings/comments
  to every function just to document the obvious.
- Keep stdlib-only by default (`http` transport). If a change needs a new
  third-party dependency, call it out explicitly in the PR description and
  in `requirements.txt` — don't add a silent import.
- Any change to the on-disk trajectory schema (`schema_version:
  "live_run_trajectory_v1"`) or the CSV columns `summarize_results.py`
  writes is a breaking change for anyone with existing run data; bump the
  schema version string rather than changing the shape of existing fields.

## 3. Reporting issues

For both categories, a good issue includes: what you ran (exact command
line), what you expected, what you got, and whether you were on `--mock` or
a real endpoint. For real-endpoint issues, please don't paste API keys,
endpoint URLs, or raw model responses that might contain either — redact
before posting.

## 4. Documentation

Changes to `README.md` / `README_zh.md` or the stage-output docs
(`运行代码与说明/各阶段预期输出.md` / `STAGE_OUTPUTS.md`) should keep the two
language versions structurally matched (same sections, not a line-by-line
translation). If you add a real command-output excerpt, it must be copied
from an actual run — never hand-authored and labeled as program output.
