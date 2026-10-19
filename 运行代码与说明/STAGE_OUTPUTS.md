# Stage-by-stage expected output

This walks through the same 8 stages as the root README's quickstart, showing
what you should actually see at each one. Quoted output blocks are copied
verbatim from a real run in this repository, trimmed for length where noted
— nothing here is a hand-authored sample pretending to be program output.

Stages 1-4 are fully deterministic (no model, no network, or a fixed mock).
Stages 5-8 depend on a real model endpoint: **outputs will vary by model, and
nothing here promises any particular success rate.**

## Stage 1 — Install / environment check

```bash
python3 -m pip install -r requirements.txt
python3 -c "import jsonschema; print(jsonschema.__version__)"
```

Expect `4.26.0` (the only pinned dependency). You do **not** need to install
`openai` unless you intend to pass `--transport sdk` to `live_runner.py`; the
default `--transport http` only uses the standard library.

## Stage 2 — Release integrity check

Verify your checkout matches `运行代码与说明/release_manifest.json` before
trusting anything downstream — a corrupted download should fail loudly here,
not three stages later as a confusing test failure.

```bash
python3 - <<'PY'
import hashlib, json, sys
from pathlib import Path
manifest = json.loads(Path("运行代码与说明/release_manifest.json").read_text(encoding="utf-8"))
bad = []
for entry in manifest["files"]:
    data = Path(entry["path"]).read_bytes()
    if hashlib.sha256(data).hexdigest() != entry["sha256"] or len(data) != entry["bytes"]:
        bad.append(entry["path"])
print("OK" if not bad else f"MISMATCH: {bad}")
sys.exit(1 if bad else 0)
PY
```

Expect `OK`. This manifest (`kind: "repo_packaging_manifest"`) covers the
*entire current repository tree* as packaged for public release — it is
separate from `运行代码与说明/原始release_manifest_frozen.json`, which
records the original internal `kaxinguo_feasibility_v1` release's 33 files
only; the new manifest's `frozen_core_manifest_sha256` field hashes that
original manifest file itself for traceability.

## Stage 3 — Offline protocol/fixture regression

```bash
python3 运行代码与说明/verify_release.py
```

This prints a single JSON summary to stdout and exits 0 on success. A real
run produces (verbatim, from this repository's committed
`verification_report.json`):

```json
{
  "protocol_version": "kaxinguo_feasibility_v1",
  "scope": "FROZEN_PROTOCOL_AND_HAND_AUTHORED_FIXTURE_REGRESSION",
  "unique_goals": 10,
  "scenarios": 48,
  "json_schema_validator": "jsonschema 4.26.0",
  "reference_data_cases_passed": 48,
  "step_adapter_replay_cases_passed": 48,
  "hand_authored_bt_count": 10,
  "hand_authored_bt_replay_cases_passed": 48,
  "single_information_observations": 85,
  "simple_replies": 14,
  "reference_calls_by_type": {
    "MOVE_TO": 98, "OBSERVE": 85, "SAY": 49, "FINISH": 48, "FOLLOW": 3, "ASK": 14
  },
  "request_pairs_checked": 96,
  "negative_protocol_checks_passed": 16,
  "model_called": false,
  "model_planning_accuracy": null,
  "vllm_backend_schema_acceptance_tested": false,
  "real_robot_or_vision_tested": false,
  "general_free_text_semantic_scorer_implemented": false
}
```

No model is called (`model_called: false`); this only proves the protocol
implementation and the 48 hand-authored fixtures agree with each other.

## Stage 4 — HTTP mock walkthrough

```bash
python3 运行代码与说明/mock_server.py --bt-test-id episode_001 --step-test-id episode_016
```

prints, and then blocks serving:

```
[MOCK] serving http://127.0.0.1:8800/v1/chat/completions bt=episode_001 step=episode_016
```

In another terminal (using a scratch `--out-dir` here, so your own run
doesn't mix with the examples already committed at `运行结果/mock_runs/` —
see the note below):

```bash
python3 运行代码与说明/live_runner.py --mock --architecture bt --test-id episode_001 \
  --model mock-model --endpoint http://127.0.0.1:8800 --out-dir /tmp/kaxinguo_mock_walkthrough
python3 运行代码与说明/live_runner.py --mock --architecture step --test-id episode_016 \
  --model mock-model --endpoint http://127.0.0.1:8800 --out-dir /tmp/kaxinguo_mock_walkthrough
```

Each call prints one line per trajectory it writes, e.g. `<path>: FINISHED`.

The committed example trajectories in `运行结果/mock_runs/` are real output
from this exact walkthrough, run once on port 8801 instead of 8800 (the only
difference it makes is the `endpoint` field below). If you omit `--out-dir`,
`live_runner.py` defaults to `运行结果/mock_runs/` too — your new run would
then land *alongside* these two committed files rather than replacing them,
and `summarize_results.py --include-mock` would report on all four together
(grouped separately per `endpoint`, since port 8800 vs. 8801 differ). Using
a scratch `--out-dir` as shown above keeps your own walkthrough isolated and
easy to inspect on its own. Trimmed excerpts from the committed examples:

BT trajectory (`episode_001_bt_*.json`) — one model call, since the whole
plan is produced up front:

```json
{
  "schema_version": "live_run_trajectory_v1",
  "architecture": "bt",
  "test_id": "episode_001",
  "task_id": "task_001",
  "is_mock_endpoint": true,
  "outcome": "FINISHED",
  "terminal_decision": {"decision": "FINISH", "outcome": "completed", "message": "已完成当前任务分支。"},
  "counters": {
    "model_calls": 1, "decisions_accepted": 4,
    "skill_calls": {"MOVE_TO": 1, "OBSERVE": 1, "FOLLOW": 0, "SAY": 1},
    "ask_count": 0
  },
  "human_review": {"criteria": {"structure_and_arguments": null, "...": null}, "reviewer": null}
}
```

STEP trajectory (`episode_016_step_*.json`) — one model call *per decision*
(6 total: `MOVE_TO`, `OBSERVE`, `ASK`, `FOLLOW`, `SAY`, `FINISH`):

```json
{
  "schema_version": "live_run_trajectory_v1",
  "architecture": "step",
  "test_id": "episode_016",
  "task_id": "task_005",
  "is_mock_endpoint": true,
  "outcome": "FINISHED",
  "terminal_decision": {"decision": "FINISH", "outcome": "completed", "message": "已完成当前任务分支。"},
  "counters": {
    "model_calls": 6, "decisions_accepted": 6,
    "skill_calls": {"MOVE_TO": 1, "OBSERVE": 1, "FOLLOW": 1, "SAY": 1},
    "ask_count": 1
  },
  "human_review": {"criteria": {"structure_and_arguments": null, "...": null}, "reviewer": null}
}
```

Both have `human_review.criteria` entirely `null` — the mock walkthrough
proves the pipeline runs end-to-end, nothing about semantic correctness.

## Stage 5 — Real-endpoint preflight (model-dependent)

```bash
export OPENAI_API_KEY=sk-...
python3 运行代码与说明/live_runner.py --architecture both --test-id episode_001 \
  --model <your-model> --endpoint https://your-endpoint/v1
```

`live_runner.py` validates its configuration and builds one sample request
per architecture **before** opening a transport or making any call (see the
"Preflight configuration" step in `main()`). If this fails, it's a
request-construction/schema problem, not your model's fault yet — fix that
first. From here on, actual console output depends entirely on your model
and endpoint; this document cannot show you a real transcript for it.

## Stage 6 — Small test-id run (model-dependent)

Same command as Stage 5, without `--mock`. Watch for the printed
`<path>: <outcome>` line. Possible `outcome` values (from `live_runner.py`):
`FINISHED`, `NEEDS_RESOLUTION`, `STRUCTURAL_FAILURE`,
`DECISION_LIMIT_EXCEEDED`, `TRANSPORT_ERROR`, `ENVIRONMENT_REJECTED`. Only
`FINISHED` can ever become a semantic pass, and even then only after human
review (Stage 8) — none of these outcome strings are a verdict by
themselves.

## Stage 7 — Full `--all` run (model-dependent)

```bash
python3 运行代码与说明/live_runner.py --architecture both --all \
  --model <your-model> --endpoint https://your-endpoint/v1
```

This runs all 48 scenarios under both architectures (96 trajectories). Cost
and latency scale with your model and endpoint — there is no fixed expected
runtime to quote here.

## Stage 8 — Review & summarize

```bash
python3 运行代码与说明/summarize_results.py --runs-dir 运行结果/live_runs
```

For the mock walkthrough specifically, use `--include-mock` so those
trajectories aren't silently dropped. The command below points at the two
examples already committed at `运行结果/mock_runs/`, not at your own Stage 4
run (which, if you followed the `--out-dir /tmp/kaxinguo_mock_walkthrough`
example above, lives separately — summarize that location the same way by
pointing `--runs-dir` at it instead):

```bash
python3 运行代码与说明/summarize_results.py --runs-dir 运行结果/mock_runs --include-mock
```

Real, committed output of exactly that command — `scenarios.csv` header plus
its first data row:

```
run_id,started_at,test_id,task_id,architecture,model_name,endpoint,transport,is_mock_endpoint,settings_id,outcome,classification,bt_model_call_reused_from,selected_for_metrics,structure_and_arguments,required_observation_evidence,correct_branch,all_required_actions,constraints_respected,completion_at_correct_time,notes,reviewer,reviewed_at,model_calls,prompt_tokens,completion_tokens,total_tokens,usage_missing_calls,model_latency_seconds,elapsed_seconds
167395ed-0998-48a5-9bde-341cdf39ce43,2026-10-15T07:55:57.996987+00:00,episode_001,task_001,bt,mock-model,http://127.0.0.1:8801/v1/chat/completions,http,true,50a4f83f...,FINISHED,PENDING_HUMAN_REVIEW,,true,,,,,,,,,,1,0,0,0,0,0.0021586750008282...,0.1622840970012475...
```

and `goals.csv` in full:

```
architecture,model_name,endpoint,transport,is_mock_endpoint,settings_id,goals_all_scenarios_pass,total_goals,macro_pass_rate
bt,mock-model,http://127.0.0.1:8801/v1/chat/completions,http,true,50a4f83f...,0,10,0.0
step,mock-model,http://127.0.0.1:8801/v1/chat/completions,http,true,50a4f83f...,0,10,0.0
```

`classification` is `PENDING_HUMAN_REVIEW`, not `HUMAN_CONFIRMED_PASS`, and
`macro_pass_rate` is `0.0` — because no human has reviewed these mock
trajectories (the point of the mock walkthrough is to prove the plumbing
works, not to produce a score). To actually move a trajectory to
`HUMAN_CONFIRMED_PASS`/`HUMAN_CONFIRMED_FAIL`, open its JSON file and set all
six keys under `human_review.criteria` (`structure_and_arguments`,
`required_observation_evidence`, `correct_branch`, `all_required_actions`,
`constraints_respected`, `completion_at_correct_time`) to `true`/`false`,
then re-run `summarize_results.py` — it merges your review into the existing
`scenarios.csv` row for that `run_id` without erasing it on a later rescan.

For real-endpoint runs, your `scenarios.csv`/`task_pass_rates.csv`/
`goals.csv` will contain your model's actual outcomes and (after review)
pass rates — this document cannot and does not predict what those will be.
