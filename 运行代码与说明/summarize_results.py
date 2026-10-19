"""Summarize trajectories; FINISHED alone is never a semantic pass.

Output is CSV only; a second --out-format could be added later if needed.
"""
import argparse
from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_VERSION = "live_run_trajectory_v1"
CRITERIA = (
    "structure_and_arguments", "required_observation_evidence",
    "correct_branch", "all_required_actions", "constraints_respected",
    "completion_at_correct_time",
)
GROUP = ("architecture", "model_name", "endpoint", "transport",
         "is_mock_endpoint", "settings_id")
COSTS = ("model_calls", "prompt_tokens", "completion_tokens", "total_tokens",
         "usage_missing_calls", "model_latency_seconds", "elapsed_seconds")
FIELDS = (
    "run_id", "started_at", "test_id", "task_id", *GROUP,
    "outcome", "classification", "bt_model_call_reused_from",
    "selected_for_metrics", *CRITERIA, "notes", "reviewer", "reviewed_at", *COSTS,
)


def review_value(value):
    if value is None or value == "":
        return ""
    if value is True or value == "true":
        return "true"
    if value is False or value == "false":
        return "false"
    raise ValueError(f"Review criteria must be boolean or null, got {value!r}")


def classify(row):
    if row["outcome"] != "FINISHED":
        return "STRUCTURAL_FAIL"
    if any(row.get(key, "") == "" for key in CRITERIA):
        return "PENDING_HUMAN_REVIEW"
    return ("HUMAN_CONFIRMED_PASS" if all(row[key] == "true" for key in CRITERIA)
            else "HUMAN_CONFIRMED_FAIL")


def row_from_run(run):
    config = json.dumps(run["config_snapshot"], sort_keys=True, ensure_ascii=False)
    row = {key: run.get(key, "") for key in FIELDS}
    row["settings_id"] = hashlib.sha256(config.encode("utf-8")).hexdigest()
    row["is_mock_endpoint"] = "true" if run["is_mock_endpoint"] else "false"
    row["bt_model_call_reused_from"] = run.get("bt_model_call_reused_from") or ""
    row["selected_for_metrics"] = "false"
    review = run.get("human_review") or {}
    criteria = review.get("criteria") or {}
    for key in CRITERIA:
        row[key] = review_value(criteria.get(key))
    for key in ("notes", "reviewer", "reviewed_at"):
        row[key] = review.get(key) or ""
    for key in COSTS:
        row[key] = run["counters"][key]
    row["classification"] = classify(row)
    return row


def merge_review(row, old):
    """Fill holes from the same run only; explicit new booleans win conflicts.

    A re-run has a new UUID. Its older reviewed row is retained separately;
    a verdict about a different model trajectory must not certify the new run.
    Thus blank scans cannot erase annotations, even across repeated summaries.
    """
    if old is None:
        return row
    identity = ("run_id", "test_id", "task_id", *GROUP)
    if any(str(row[key]) != str(old.get(key, "")) for key in identity):
        raise ValueError("run_id was reused for different trajectory metadata")
    for key in CRITERIA:
        if row[key] == "":
            row[key] = review_value(old.get(key))
    for key in ("notes", "reviewer", "reviewed_at"):
        if not row[key]:
            row[key] = old.get(key, "")
    row["classification"] = classify(row)
    return row


def write_csv(path, fields, rows):
    # Replacement is atomic; an interrupted write leaves the previous table intact.
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def summarize(runs_dir, out_dir, include_mock=False):
    catalog = json.loads((ROOT / "数据集及参考计划" / "test_inputs.json")
                         .read_text(encoding="utf-8"))["cases"]
    expected = defaultdict(set)
    task_by_test = {}
    for case in catalog:
        expected[case["task_id"]].add(case["test_id"])
        task_by_test[case["test_id"]] = case["task_id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    scenarios_path = out_dir / "scenarios.csv"
    rows = {}
    if scenarios_path.exists():
        with scenarios_path.open(encoding="utf-8-sig", newline="") as handle:
            for old in csv.DictReader(handle):
                if old.get("is_mock_endpoint") == "true" and not include_mock:
                    continue
                for key in CRITERIA:
                    old[key] = review_value(old.get(key))
                old["selected_for_metrics"] = "false"
                old["classification"] = classify(old)
                rows[old["run_id"]] = old

    scanned = set()
    for path in sorted(runs_dir.glob("*.json")):
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            print(f"Skipping unreadable JSON {path.name}: {exc}", file=sys.stderr)
            continue
        if not isinstance(run, dict) or run.get("schema_version") != SCHEMA_VERSION:
            continue
        if type(run.get("is_mock_endpoint")) is not bool:
            raise ValueError(f"{path}: missing/invalid is_mock_endpoint")
        if run["is_mock_endpoint"] and not include_mock:
            continue
        if task_by_test.get(run["test_id"]) != run["task_id"]:
            raise ValueError(f"{path}: unknown or mismatched test_id/task_id")
        if run["architecture"] not in {"bt", "step"}:
            raise ValueError(f"{path}: unknown architecture")
        row = row_from_run(run)
        run_id = row["run_id"]
        if run_id in scanned:
            # Copies of one trajectory count once; conflicting copies fail loudly.
            old = rows[run_id]
            for key in ("outcome", "bt_model_call_reused_from", *COSTS):
                if str(old[key]) != str(row[key]):
                    raise ValueError(f"Conflicting copies of run {run_id}")
        rows[run_id] = merge_review(row, rows.get(run_id))
        scanned.add(run_id)

    # Preserve historical reviews, but only currently scanned runs enter metrics.
    # For retries: select latest started_at, with UUID as a deterministic tie-break.
    latest = {}
    groups = defaultdict(list)
    for run_id in sorted(scanned):
        row = rows[run_id]
        group = tuple(row[k] for k in GROUP)
        groups[group].append(row)
        key = (group, row["test_id"])
        if key not in latest or (row["started_at"], run_id) > (
                latest[key]["started_at"], latest[key]["run_id"]):
            latest[key] = row
    for row in latest.values():
        row["selected_for_metrics"] = "true"

    task_rows, cost_rows, goal_rows = [], [], []
    for group, runs in sorted(groups.items()):
        metadata = dict(zip(GROUP, group))
        all_passed = 0
        rates = []
        for task_id, test_ids in sorted(expected.items()):
            selected = [latest[(group, test_id)] for test_id in sorted(test_ids)
                        if (group, test_id) in latest]
            passed = sum(r["classification"] == "HUMAN_CONFIRMED_PASS" for r in selected)
            rate = passed / len(test_ids)
            rates.append(rate)
            complete = passed == len(test_ids)
            all_passed += int(complete)
            task_rows.append({
                **metadata, "task_id": task_id, "scenario_count": len(test_ids),
                "scenarios_run": len(selected), "human_confirmed_pass": passed,
                "pass_rate": rate, "all_scenarios_pass": str(complete).lower(),
            })
        goal_rows.append({
            **metadata, "goals_all_scenarios_pass": all_passed,
            "total_goals": len(expected), "macro_pass_rate": sum(rates) / len(rates),
        })
        # BT cached executions share the originating planning run; STEP's many
        # calls belong to one logical planning episode per trajectory.
        logical_ids = {r["bt_model_call_reused_from"] or r["run_id"] for r in runs}
        missing_sources = {
            r["bt_model_call_reused_from"] for r in runs
            if r["bt_model_call_reused_from"]
            and r["bt_model_call_reused_from"] not in scanned
        }
        totals = {}
        for key in COSTS:
            convert = float if key.endswith("seconds") else int
            totals[key] = sum(convert(r[key]) for r in runs)
        cost_rows.append({
            **metadata, "trajectory_count": len(runs),
            "actual_api_calls": totals.pop("model_calls"),
            "logical_planning_episodes": len(logical_ids),
            "missing_cached_source_runs": len(missing_sources), **totals,
        })
    write_csv(scenarios_path, FIELDS,
              [{k: row.get(k, "") for k in FIELDS}
               for row in sorted(rows.values(), key=lambda r: (
                   tuple(r[k] for k in GROUP), r["test_id"], r["started_at"], r["run_id"]))])
    write_csv(out_dir / "task_pass_rates.csv", (*GROUP, "task_id", "scenario_count",
              "scenarios_run", "human_confirmed_pass", "pass_rate", "all_scenarios_pass"),
              task_rows)
    write_csv(out_dir / "costs.csv", (*GROUP, "trajectory_count", "actual_api_calls",
              "logical_planning_episodes", "missing_cached_source_runs", *COSTS[1:]),
              cost_rows)
    write_csv(out_dir / "goals.csv", (*GROUP, "goals_all_scenarios_pass",
              "total_goals", "macro_pass_rate"), goal_rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--include-mock", action="store_true")
    args = parser.parse_args()
    if not args.runs_dir.is_dir():
        parser.error("--runs-dir must be an existing directory")
    summarize(args.runs_dir, args.out_dir or args.runs_dir, args.include_mock)


if __name__ == "__main__":
    main()
