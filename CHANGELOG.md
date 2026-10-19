# Changelog

All notable changes to this **repository's packaging** are recorded here.

This changelog tracks the packaging/release of the public repo itself — a
separate version axis from the two identifiers frozen inside the dataset,
prompt, schema, and config files themselves: `protocol_version`
(`kaxinguo_feasibility_v1`) and `dataset_id`
(`home_branch_planning_dataset_v3`), both set in
`提示词与接口配置/release_config.json`. These are two distinct strings, not
aliases of each other. Packaging releases (below) can happen without either
frozen identifier changing, and vice versa: see `CONTRIBUTING.md` for when
the frozen identifiers themselves must be bumped.

## [1.0.0] - 2026-10-15

Initial public packaging release.

### Added
- Bilingual root `README.md` / `README_zh.md` replacing the previous
  internal packaging notes as the primary entry point. The former
  `运行代码与说明/README_zh.md` is now a short navigation stub pointing here
  and at the new per-topic docs.
- `运行代码与说明/各阶段预期输出.md` and `STAGE_OUTPUTS.md`: an 8-stage,
  bilingual walkthrough of expected output at each step, from environment
  setup through `summarize_results.py`, quoting real excerpts from the
  committed `运行结果/mock_runs/` example trajectories and CSVs.
- `live_runner.py`, `mock_server.py`, `summarize_results.py`: new tooling
  for actually running an evaluation against a reader's own model endpoint
  (or the committed mock fixtures) and summarizing results into CSV. These
  are **not** part of the frozen `kaxinguo_feasibility_v1` protocol/dataset
  — they are evaluation harness code that operates on top of it, and can
  evolve independently (see `CONTRIBUTING.md` §2).
- `LICENSE` (MIT, code) and `LICENSE-DATA` (CC BY 4.0, dataset/prompts/
  schemas/docs) as two separate license files, since a single root
  `LICENSE` would misleadingly imply one license covers everything.
- `CITATION.cff` for citing this release (author/affiliation fields are
  placeholders pending the maintainer's decision).
- `运行代码与说明/release_manifest.json`, regenerated to cover the current
  state of the entire packaged repository (sha256 per file), distinct from
  the original internal `kaxinguo_feasibility_v1` release manifest.
- `.github/workflows/ci.yml`: runs the offline protocol regression
  (`verify_release.py`) plus a full mock-server walkthrough
  (`mock_server.py` + `live_runner.py --mock` + `summarize_results.py`) on
  every push/PR, with no secrets and no real model required.

### Unchanged
- The dataset, prompt, and config files under `数据集及参考计划/` and
  `提示词与接口配置/`, and the seven original scripts in
  `运行代码与说明/` (`adapters.py`, `protocol.py`, `observation_render.py`,
  `generate_schemas.py`, `build_reference_plans.py`, `verify_release.py`)
  plus `verification_report.json`, are byte-identical to the internal
  `kaxinguo_feasibility_v1` frozen release. See
  `运行代码与说明/原始release_manifest_frozen.json` for that release's
  original per-file SHA-256 hashes (33 files).
- The frozen identifiers themselves — `protocol_version:
  "kaxinguo_feasibility_v1"` and `dataset_id:
  "home_branch_planning_dataset_v3"` — are unchanged by this packaging
  release.

[1.0.0]: #100---2026-10-15
