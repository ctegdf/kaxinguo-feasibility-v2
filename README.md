# kaxinguo_feasibility_v1

A frozen dataset and protocol for comparing two LLM robot-task-planning
architectures — **BT** (one-shot behavior tree) and **STEP** (turn-by-turn
decisions) — across **10 goals / 48 scenarios**, plus tooling to actually run
and summarize an evaluation of your own model against them.

中文说明见 [README_zh.md](README_zh.md)。

## What this is

`kaxinguo_feasibility_v1` is a hand-authored benchmark for a home-robot
planning task: given a semantic map, a natural-language instruction, and a
set of physical skills (`MOVE_TO`, `OBSERVE`, `FOLLOW`, `SAY`), an LLM must
produce a plan that correctly branches on scene facts it hasn't observed yet.
Two architectures are compared on the *same* 48 scenarios:

- **BT** — the model sees the task once and must emit a complete behavior
  tree up front, expressing every not-yet-observed outcome as a conditional
  branch. The executor only interprets the tree; there is no re-planning.
- **STEP** — the model is called again after every completed action/ASK,
  and emits one decision at a time (`CALL` a skill, `ASK` the user, or
  `FINISH`).

This repository contains:

- The frozen dataset and protocol: `数据集及参考计划/` (scenarios, reference
  plans, validation/oracle data) and `提示词与接口配置/` (system prompts,
  JSON Schemas, shared semantic context, release config).
- The protocol implementation that both the original internal release and
  this package depend on: `运行代码与说明/{adapters,protocol,
  observation_render,generate_schemas,build_reference_plans,verify_release}.py`.
- **New tooling**, written for this public package, to actually exercise the
  protocol against a model: `运行代码与说明/live_runner.py` (runs episodes
  against an OpenAI-compatible endpoint, or a mock), `运行代码与说明/
  mock_server.py` (serves two fixed, hand-authored demo responses — no model,
  no network dependency), and `运行代码与说明/summarize_results.py` (turns a
  directory of run trajectories into CSV tables).

## Scope and non-goals — read this before you draw conclusions

This repository's own offline regression check
(`运行代码与说明/verify_release.py`) validates the protocol implementation
and the hand-authored fixtures against each other. As of the committed
`运行代码与说明/verification_report.json`:

```
"scope": "FROZEN_PROTOCOL_AND_HAND_AUTHORED_FIXTURE_REGRESSION"
"unique_goals": 10, "scenarios": 48
"reference_data_cases_passed": 48
"step_adapter_replay_cases_passed": 48
"hand_authored_bt_replay_cases_passed": 48
"model_called": false
"model_planning_accuracy": null
"vllm_backend_schema_acceptance_tested": false
"real_robot_or_vision_tested": false
"general_free_text_semantic_scorer_implemented": false
```

In plain terms:

- **Verified**: the protocol's structural/branching rules and the 48
  hand-authored fixtures are internally consistent and replay cleanly
  through the adapters and interpreter. This is a *regression* check against
  fixtures the authors wrote, not a model evaluation.
- **Not verified by anything in this repository**: no real LLM has been
  called against this package (`model_called: false`); no real robot or
  vision system was ever involved — perception in every scenario is a
  synthetic semantic oracle, not images (`real_robot_or_vision_tested:
  false`); and no backend has been confirmed to actually honor the
  `structured_outputs` request extension this protocol relies on
  (`vllm_backend_schema_acceptance_tested: false`).
- **This is not a generic automated semantic scorer.** There is no free-text
  judge (`general_free_text_semantic_scorer_implemented: false`).
  `summarize_results.py` can tell you whether a trajectory finished
  structurally (`outcome: "FINISHED"`), but it can **never** mark a
  trajectory `HUMAN_CONFIRMED_PASS` or `HUMAN_CONFIRMED_FAIL` on its own — a
  human must fill in six boolean review criteria per trajectory first (see
  Step 5 below). `FINISHED` alone is not a semantic pass.
- **A faithful paraphrase can be rejected before it ever reaches human
  review.** Where this protocol requires a prior spoken report before an
  `ASK` (the `required_reports` rule in `validation_data.json`'s hidden
  scenarios), the frozen oracle checks for the exact fixed `summary` text
  as a substring of what was said — not semantic equivalence. A model that
  truthfully reports the same fact in different words gets
  `ENVIRONMENT_REJECTED` → `STRUCTURAL_FAIL`, with no path to
  `human_review.criteria` at all. This is inherited from the frozen
  protocol/oracle (`运行代码与说明/verify_release.py`'s `SemanticOracle`,
  not modified by this package); if you see
  `QUESTION_BEFORE_REQUIRED_REPORT` on an otherwise-sensible run, check the
  model's literal wording before concluding it made a reasoning error.

## "OpenAI-compatible" — more precisely

`live_runner.py` sends standard Chat Completions requests (`messages`,
`temperature`, `max_tokens`, `stream`), but it also attaches a
`structured_outputs` field carrying this protocol's output JSON Schema on
every request (see `adapters.py:make_request`) — a **vLLM-specific request
extension**, documented at the link embedded in
`提示词与接口配置/release_config.json`'s `backend_request_documentation`
field: https://docs.vllm.ai/en/latest/features/structured_outputs/. This is
not part of the vendor-neutral OpenAI API surface.

If your backend doesn't understand `structured_outputs`, two things can
happen: it rejects the request outright (loud, safe — you'll see a
`TRANSPORT_ERROR` or `STRUCTURAL_FAILURE` outcome and can choose a fallback
consciously), or it silently ignores the unknown field and answers without
the schema constraint (quiet, and this is the dangerous case: you would be
running an unconstrained chat completion against a protocol designed around
guaranteed-valid structured output, while still logging it as the same
experimental condition as a schema-constrained run). If you plan to target a
backend that isn't vLLM or vLLM-compatible, check whether it supports this
extension (or an equivalent) **before** trusting results, and record which
case you're in rather than letting it default silently.

## Quickstart

### 1. Install

```bash
python3 -m pip install -r requirements.txt
```

The only required dependency is `jsonschema==4.26.0`. `live_runner.py`'s
`--transport sdk` path lazily imports the `openai` package, but that is
*not* a required dependency — the default `--transport http` is stdlib-only
(`urllib`).

### 2. Offline protocol/fixture regression (no network, no model)

```bash
python3 运行代码与说明/verify_release.py
```

This replays the 48 hand-authored fixtures through the adapters and
interpreter. Stdout prints the aggregate counts quoted above (and exits 0 on
success). Every entry the script manages to write to the `case_results`
list inside `运行代码与说明/verification_report.json` says `PASS` — there is
no `FAIL` value: a mismatch raises an uncaught exception and halts the
script immediately, before that case's entry is appended. If something
doesn't match, read the traceback and the non-zero exit code, not the
report (which will simply be missing entries from that case onward). It
does not call any model and proves nothing about model quality — only that
the protocol and fixtures agree with each other.

### 3. Mock walkthrough — no model access needed

Two fixed, hand-authored demo responses (`episode_001` for BT, `episode_016`
for STEP) are served by `mock_server.py`, explicitly marked with a
`mock_notice` field so a trajectory can never be confused with a real model
run. Start it in one terminal:

```bash
python3 运行代码与说明/mock_server.py --bt-test-id episode_001 --step-test-id episode_016
```

Then, in another terminal, run each architecture against its *matching*
test id. `--out-dir` here points at a scratch location rather than the
default `运行结果/mock_runs/`, so your own run doesn't land alongside the
two example trajectories already committed there (see
`运行代码与说明/STAGE_OUTPUTS.md` Stage 4 for what happens if you omit it):

```bash
python3 运行代码与说明/live_runner.py --mock --architecture bt --test-id episode_001 \
  --model mock-model --endpoint http://127.0.0.1:8800 --out-dir /tmp/kaxinguo_mock_walkthrough

python3 运行代码与说明/live_runner.py --mock --architecture step --test-id episode_016 \
  --model mock-model --endpoint http://127.0.0.1:8800 --out-dir /tmp/kaxinguo_mock_walkthrough
```

Note: `--architecture both` is accepted with a single `--test-id`, but it
means "run *this one* test case under both architectures" — not "run two
different test cases." The mock server only serves `episode_001` under the
BT demo and `episode_016` under the STEP demo (different `task_id`s), so
pairing them requires the two separate calls above. Against a real endpoint
(Step 4), `--architecture both --test-id <id>` is exactly what you want — it
asks your model to solve the same case both ways.

This is genuinely what you'll get — two real committed example trajectories
are checked in at `运行结果/mock_runs/`, generated exactly this way:
`outcome: "FINISHED"` for both, 1 model call for the BT episode (one-shot
plan) and 6 for the STEP episode (one call per decision, including the
`ASK`), and a `human_review.criteria` block that is entirely `null` —
nothing is marked PASS or FAIL automatically.

### 4. Point at your own endpoint

Export your API key under whatever name `--api-key-env` expects (default
`OPENAI_API_KEY`) — **never pass it on the command line**:

```bash
export OPENAI_API_KEY=sk-...

python3 运行代码与说明/live_runner.py --architecture both --test-id episode_001 \
  --model <your-model-name> --endpoint https://your-endpoint/v1
```

`--endpoint` must be an `http(s)://` URL with no embedded credentials, query
string, or fragment — anything else is rejected (`completion_endpoint()`).
The path is flexible: a bare host, `.../v1`, a custom path, or an already-
complete `.../v1/chat/completions` URL are all accepted; whichever you give
it is normalized to end in `/v1/chat/completions` (appending `/chat/
completions` if it already ends in `/v1`, or `/v1/chat/completions`
otherwise, unless it already ends in `/chat/completions`). Once one test id
looks right, run everything:

```bash
python3 运行代码与说明/live_runner.py --architecture both --all \
  --model <your-model-name> --endpoint https://your-endpoint/v1
```

Trajectories land in `运行结果/live_runs/` by default (override with
`--out-dir`).

### 5. Summarize and review

```bash
python3 运行代码与说明/summarize_results.py --runs-dir 运行结果/live_runs
```

`--include-mock` defaults to off specifically so mock-walkthrough data never
silently pollutes real metrics; pass it only when you deliberately want to
summarize the mock walkthrough itself, e.g.:

```bash
python3 运行代码与说明/summarize_results.py --runs-dir 运行结果/mock_runs --include-mock
```

This example summarizes the two trajectories already committed at
`运行结果/mock_runs/` (generated exactly as in Step 3). If you ran Step 3
yourself with its `--out-dir /tmp/kaxinguo_mock_walkthrough`, point
`--runs-dir` there instead to summarize your own run rather than the
committed example — see `运行代码与说明/STAGE_OUTPUTS.md` Stage 8 for both
cases spelled out.

This writes four CSVs (`scenarios.csv`, `task_pass_rates.csv`, `costs.csv`,
`goals.csv`). The `classification` column in `scenarios.csv` takes one of
four values:

| Value | Meaning |
|---|---|
| `STRUCTURAL_FAIL` | `outcome != "FINISHED"` — the episode never reached a valid completion structurally. |
| `PENDING_HUMAN_REVIEW` | Finished structurally, but at least one of the six `human_review.criteria` is still unset. |
| `HUMAN_CONFIRMED_PASS` | Finished structurally AND a human has set all six criteria to `true`. |
| `HUMAN_CONFIRMED_FAIL` | Finished structurally, all six criteria are set, and at least one is `false`. |

Nothing becomes `HUMAN_CONFIRMED_PASS` or `HUMAN_CONFIRMED_FAIL` until a
human opens each trajectory JSON and fills in `human_review.criteria`'s six
boolean keys by hand: `structure_and_arguments`,
`required_observation_evidence`, `correct_branch`, `all_required_actions`,
`constraints_respected`, `completion_at_correct_time` — matching the six
success criteria this protocol defines for a scenario. There is no
automatic judge anywhere in this pipeline.

## Repository layout

| Path | Contents |
|---|---|
| `数据集及参考计划/` | The frozen dataset: public test inputs, hidden validation/oracle data, hand-authored reference plans, coverage summary. |
| `提示词与接口配置/` | The frozen protocol surface: system prompts, JSON Schemas for all four message shapes, shared semantic map/context, and `release_config.json`. |
| `运行代码与说明/` | Protocol implementation (`adapters.py`, `protocol.py`, `observation_render.py`, `generate_schemas.py`, `build_reference_plans.py`, `verify_release.py` + its `verification_report.json`), the new evaluation tooling (`live_runner.py`, `mock_server.py`, `summarize_results.py`), stage-by-stage expected-output docs, and the release manifests. |
| `运行结果/mock_runs/` | Real, committed example output from the mock walkthrough — two trajectory JSONs and four summary CSVs. |
| `.github/workflows/` | CI that runs the offline regression and the full mock walkthrough on every push/PR. |

The Chinese directory names are the dataset's original names and are kept
as-is throughout this release; this README explains each one rather than
renaming them.

## License

This repository carries **two separate licenses** — a single root `LICENSE`
would be misleading here, since code and data have different legal content:

| Content | License | File |
|---|---|---|
| `.py` source files (protocol implementation + evaluation tooling) | MIT | [`LICENSE`](LICENSE) |
| Dataset, prompts, schemas, configs, and documentation | CC BY 4.0 | [`LICENSE-DATA`](LICENSE-DATA) |

## Citing this release

See [`CITATION.cff`](CITATION.cff). Attributed to `ctegdf` (the maintainer's
chosen public handle, recorded as a CFF `entity` rather than a parsed personal
name).

## More

- [`CONTRIBUTING.md`](CONTRIBUTING.md) — how to report dataset/protocol
  issues (as new versions, not in-place edits) vs. propose tooling changes.
- [`CHANGELOG.md`](CHANGELOG.md) — this packaging release vs. the frozen
  `kaxinguo_feasibility_v1` protocol/dataset version (two separate axes).
- [`运行代码与说明/各阶段预期输出.md`](运行代码与说明/各阶段预期输出.md) /
  [`运行代码与说明/STAGE_OUTPUTS.md`](运行代码与说明/STAGE_OUTPUTS.md) — what
  to expect at each of the 8 stages above, with real quoted output.
