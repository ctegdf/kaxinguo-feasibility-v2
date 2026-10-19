"""Live protocol runner. Hidden fixtures are accessible only to the oracle built
by main(); request-construction code touches only the public test_inputs.json case.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
from http.client import HTTPException
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from adapters import (make_bt_input, make_step_input, make_request,
                      accept_step_output, append_completed_event)
from protocol import execute_tree, validate, PlanError, OutOfScopeInput, VERSION
from verify_release import SemanticOracle

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "数据集及参考计划"
CONFIG_DIR = ROOT / "提示词与接口配置"
SCHEMA_VERSION = "live_run_trajectory_v1"
CRITERIA = (
    "structure_and_arguments", "required_observation_evidence",
    "correct_branch", "all_required_actions", "constraints_respected",
    "completion_at_correct_time",
)
SKILLS = ("MOVE_TO", "OBSERVE", "FOLLOW", "SAY")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def completion_endpoint(endpoint):
    parts = urlsplit(endpoint)
    if (parts.scheme not in {"http", "https"} or not parts.netloc
            or parts.username or parts.password or parts.query or parts.fragment):
        raise ValueError("endpoint must be an HTTP(S) URL without credentials/query/fragment")
    endpoint = endpoint.rstrip("/")
    if endpoint.endswith("/chat/completions"):
        return endpoint
    return endpoint + ("/chat/completions" if endpoint.endswith("/v1")
                       else "/v1/chat/completions")


class TransportError(Exception):
    def __init__(self, message, raw_response=None):
        super().__init__(message)
        self.raw_response = raw_response


class Transport:
    """Sends an OpenAI-compatible chat-completion request over SDK or raw HTTP.

    Carries no notion of mock vs. real endpoints; callers distinguish the two
    only through the trajectory's `is_mock_endpoint` flag, which is set from
    `--mock` and confirmed by the `mock_notice` field a mock server's response
    envelope carries.
    """

    def __init__(self, kind, endpoint, api_key):
        self.kind = kind
        self.endpoint = completion_endpoint(endpoint)
        self.api_key = api_key
        self.client = None
        if kind == "sdk":
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise ValueError(
                    "SDK transport requires optional package 'openai'; use --transport http") from exc
            self.client = OpenAI(
                base_url=self.endpoint.removesuffix("/chat/completions"),
                api_key=api_key or "unused-local-key", max_retries=0, timeout=120.0,
            )

    def call(self, request):
        if self.kind == "sdk":
            try:
                response = self.client.chat.completions.create(**request)
                return response.model_dump(mode="json")
            except Exception as exc:
                # Do not serialize SDK exceptions that may include authorization.
                status = getattr(exc, "status_code", None)
                raise TransportError(f"SDK {type(exc).__name__}; status={status}") from exc
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        req = Request(self.endpoint, data=json.dumps(request).encode("utf-8"),
                      headers=headers, method="POST")
        try:
            with urlopen(req, timeout=120) as response:
                return response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise TransportError(f"HTTP {exc.code}", body) from exc
        except (URLError, OSError, TimeoutError, HTTPException) as exc:
            raise TransportError(f"Network failure: {type(exc).__name__}") from exc

    def close(self):
        if self.client:
            self.client.close()


def public_request(model, architecture, dynamic_input, config, transport_kind):
    # This boundary receives public input / completed events, never hidden cases.
    request = make_request(
        model, architecture, dynamic_input,
        max_tokens=config["initial_max_tokens"][architecture], transport=transport_kind,
    )
    if request["temperature"] != config["temperature"]:
        raise ValueError(
            "Configuration mismatch: adapters.make_request temperature="
            f'{request["temperature"]!r}, release_config temperature={config["temperature"]!r}')
    return request


def new_turn(trajectory):
    turn = dict(request_sent=None, raw_model_response=None, latency_seconds=0.0,
                usage=None, parse_error=None, validation_error=None,
                decision=None, environment_call=None)
    trajectory["turns"].append(turn)
    return turn


def failure(trajectory, turn, outcome, phase, exc):
    message = str(exc)
    code = message.split(":", 1)[0]
    if phase in {"response_json", "model_json"}:
        kind = "JSON_PARSE"
        turn["parse_error"] = message
    elif "BRANCH" in code or "GUARDED_SEQUENCE" in code:
        kind = "BRANCH_COVERAGE_OR_STRUCTURE"
        turn["validation_error"] = message
    else:
        kind = "SCHEMA" if code.startswith("SCHEMA_") else phase.upper()
        if outcome not in {"TRANSPORT_ERROR", "ENVIRONMENT_REJECTED"}:
            turn["validation_error"] = message
    trajectory.update(outcome=outcome, error_phase=phase,
                      error_detail={"kind": kind, "code": code, "message": message})


def protocol_failure(trajectory, turn, phase, exc):
    outcome = ("DECISION_LIMIT_EXCEEDED"
               if str(exc).startswith("DECISION_LIMIT_EXCEEDED")
               else "STRUCTURAL_FAILURE")
    failure(trajectory, turn, outcome, phase, exc)


def get_output(trajectory, turn, dynamic_input, config, transport, cache):
    architecture = trajectory["architecture"]
    request = public_request(trajectory["model_name"], architecture,
                             dynamic_input, config, transport.kind)
    # Exact public request identity also prevents reuse across models/settings.
    key = json.dumps(request, sort_keys=True, ensure_ascii=False)
    cached = cache.get(key) if architecture == "bt" else None
    counters = trajectory["counters"]
    if cached is not None:
        source_id, raw = cached
        trajectory["bt_model_call_reused_from"] = source_id
        turn["model_response_reused_from"] = source_id
        raw = deepcopy(raw)
    else:
        turn["request_sent"] = deepcopy(request)  # Never includes HTTP auth headers.
        counters["model_calls"] += 1
        started = time.perf_counter()
        try:
            raw = transport.call(request)
        except TransportError as exc:
            turn["raw_model_response"] = exc.raw_response
            counters["usage_missing_calls"] += 1
            failure(trajectory, turn, "TRANSPORT_ERROR", "transport", exc)
            return None
        finally:
            elapsed = time.perf_counter() - started
            turn["latency_seconds"] = elapsed
            counters["model_latency_seconds"] += elapsed
        if architecture == "bt":
            cache[key] = (trajectory["run_id"], deepcopy(raw))
    turn["raw_model_response"] = raw
    try:
        envelope = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(envelope, dict):
            raise ValueError("Chat completion response must be an object")
    except (ValueError, TypeError) as exc:
        if cached is None:
            counters["usage_missing_calls"] += 1
        failure(trajectory, turn, "STRUCTURAL_FAILURE", "response_json", exc)
        return None
    if envelope.get("mock_notice"):
        trajectory["is_mock_endpoint"] = True
    usage = envelope.get("usage")
    turn["usage"] = usage
    if cached is None:
        names = ("prompt_tokens", "completion_tokens", "total_tokens")
        if isinstance(usage, dict) and all(type(usage.get(k)) is int and usage[k] >= 0 for k in names):
            for name in names:
                counters[name] += usage[name]
        else:
            counters["usage_missing_calls"] += 1
    try:
        content = envelope["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("choices[0].message.content must be a JSON string")
        return json.loads(content)
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        failure(trajectory, turn, "STRUCTURAL_FAILURE", "model_json", exc)
        return None


def environment_event(trajectory, turn, decision, environment_step):
    turn["decision"] = deepcopy(decision)
    counters = trajectory["counters"]
    counters["decisions_accepted"] += 1
    if decision["decision"] == "CALL":
        counters["skill_calls"][decision["skill"]] += 1
    elif decision["decision"] == "ASK":
        counters["ask_count"] += 1
    turn["environment_call"] = {"decision": deepcopy(decision), "actual_result": None,
                                "error": None}
    if decision["decision"] == "FINISH":
        trajectory["terminal_decision"] = deepcopy(decision)
    try:
        result = environment_step(decision)
    except (PlanError, OutOfScopeInput) as exc:
        turn["environment_call"]["error"] = str(exc)
        failure(trajectory, turn, "ENVIRONMENT_REJECTED", "oracle_step", exc)
        return None
    turn["environment_call"]["actual_result"] = deepcopy(result)
    trajectory["final_robot_state"] = deepcopy(result["robot_state"])
    return result


def run_episode(public_case, architecture, config, shared, transport, model,
                environment_step, cache, is_mock=False):
    started = time.perf_counter()
    trajectory = {
        "schema_version": SCHEMA_VERSION, "protocol_version": VERSION,
        "run_id": str(uuid4()), "started_at": datetime.now(timezone.utc).isoformat(),
        "architecture": architecture, "test_id": public_case["test_id"],
        "task_id": public_case["task_id"], "model_name": model,
        "endpoint": transport.endpoint, "transport": transport.kind,
        "is_mock_endpoint": bool(is_mock), "config_snapshot": deepcopy(config),
        "bt_model_call_reused_from": None, "turns": [], "outcome": None,
        "error_phase": None, "error_detail": None,
        "final_robot_state": deepcopy(public_case["payload"]["Robot_State"]),
        "terminal_decision": None,
        "counters": {
            "model_calls": 0, "decisions_accepted": 0,
            "skill_calls": dict.fromkeys(SKILLS, 0), "ask_count": 0,
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
            "usage_missing_calls": 0, "model_latency_seconds": 0.0,
            "elapsed_seconds": 0.0,
        },
        "human_review": {"criteria": dict.fromkeys(CRITERIA), "notes": "",
                         "reviewer": None, "reviewed_at": None},
    }
    limit = shared["Shared_Protocol"]["max_decisions_per_episode"]
    runner = None
    try:
        if architecture == "bt":
            turn = new_turn(trajectory)
            plan = get_output(trajectory, turn, make_bt_input(public_case),
                              config, transport, cache)
            if trajectory["outcome"]:
                return trajectory
            try:
                validate("bt_output", plan)
                if plan["task_id"] != public_case["task_id"]:
                    raise PlanError("WRONG_TASK_OUTPUT")
                if plan["decision"] != "PLAN":
                    trajectory["outcome"] = "NEEDS_RESOLUTION"
                    trajectory["terminal_decision"] = deepcopy(plan)
                    return trajectory
                runner = execute_tree(plan, public_case["payload"]["Robot_State"], shared)
                decision = next(runner)
            except (PlanError, OutOfScopeInput) as exc:
                protocol_failure(trajectory, turn, "bt_validation_or_start", exc)
                return trajectory
            except StopIteration:
                protocol_failure(trajectory, turn, "bt_runtime", PlanError("MISSING_FINISH"))
                return trajectory
            while True:
                result = environment_event(trajectory, turn, decision, environment_step)
                if result is None:
                    break
                previous = decision
                try:
                    decision = runner.send(result)
                except StopIteration:
                    if previous["decision"] == "FINISH":
                        trajectory["outcome"] = "FINISHED"
                    else:
                        protocol_failure(trajectory, turn, "bt_runtime", PlanError("MISSING_FINISH"))
                    break
                except (PlanError, OutOfScopeInput) as exc:
                    protocol_failure(trajectory, turn, "bt_runtime", exc)
                    break
                turn = new_turn(trajectory)  # Execution-only turn; no model call.
        else:
            context = make_step_input(public_case)
            while True:
                turn = new_turn(trajectory)
                if len(context["events"]) >= limit:
                    protocol_failure(trajectory, turn, "client_decision_guard",
                                     PlanError("DECISION_LIMIT_EXCEEDED"))
                    break
                output = get_output(trajectory, turn, context, config, transport, cache)
                if trajectory["outcome"]:
                    break
                try:
                    pending, decision = accept_step_output(context, output, shared)
                except (PlanError, OutOfScopeInput) as exc:
                    protocol_failure(trajectory, turn, "step_accept", exc)
                    break
                result = environment_event(trajectory, turn, decision, environment_step)
                if result is None:
                    break
                try:
                    context = append_completed_event(pending, decision, result, shared)
                except (PlanError, OutOfScopeInput) as exc:
                    protocol_failure(trajectory, turn, "step_append", exc)
                    break
                if decision["decision"] == "FINISH":
                    trajectory["outcome"] = "FINISHED"
                    break
        return trajectory
    finally:
        if runner is not None:
            runner.close()
        trajectory["counters"]["elapsed_seconds"] = time.perf_counter() - started


def make_environment_step(hidden_case, payload, shared):
    """The only hidden-data boundary in this module.

    `hidden_case` must already be the validation_data.json entry for this
    public case's test_id, loaded once by the caller. Only completed results
    escape through the returned callable; neither the case nor its
    reference_trace is ever returned to request-building code.
    """
    return SemanticOracle(hidden_case, deepcopy(payload), deepcopy(shared)).step


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--test-id")
    selection.add_argument("--all", action="store_true")
    parser.add_argument("--architecture", choices=("bt", "step", "both"), required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--transport", choices=("sdk", "http"), default="http")
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--max-decisions", type=int)
    args = parser.parse_args()
    out_dir = args.out_dir or (ROOT / "运行结果" / ("mock_runs" if args.mock else "live_runs"))

    config = read_json(CONFIG_DIR / "release_config.json")
    shared = read_json(CONFIG_DIR / "shared_context.json")
    if config["protocol_version"] != VERSION:
        parser.error("release_config protocol_version differs from protocol.VERSION")
    limit = (args.max_decisions if args.max_decisions is not None
             else config["max_decisions_per_episode"])
    if limit <= 0:
        parser.error("--max-decisions must be positive")
    architectures = ("bt", "step") if args.architecture == "both" else (args.architecture,)
    if "step" in architectures:
        # step_input.schema.json fixes events.maxItems; raising --max-decisions
        # past it cannot work (the (ceiling+1)th event fails schema validation
        # instead of hitting this client-side guard), so fail fast and clearly.
        ceiling = read_json(CONFIG_DIR / "schemas" / "step_input.schema.json"
                            )["properties"]["events"]["maxItems"]
        if limit > ceiling:
            parser.error(
                f"--max-decisions={limit} exceeds the frozen step_input schema's "
                f"events.maxItems={ceiling}; a STEP episode cannot run past that "
                "many completed events no matter what this flag requests."
            )
    shared["Shared_Protocol"]["max_decisions_per_episode"] = limit
    config["max_decisions_per_episode"] = limit
    config.update(model_name=args.model, endpoint=completion_endpoint(args.endpoint),
                  transport=args.transport)

    public_cases = read_json(DATA_DIR / "test_inputs.json")["cases"]
    cases = public_cases if args.all else [c for c in public_cases if c["test_id"] == args.test_id]
    if not cases:
        parser.error("Unknown --test-id")
    hidden_cases = {c["test_id"]: c for c in read_json(DATA_DIR / "validation_data.json")["cases"]}

    # Preflight configuration before opening a client or making any API call.
    for architecture in architectures:
        dynamic = make_bt_input(cases[0]) if architecture == "bt" else make_step_input(cases[0])
        public_request(args.model, architecture, dynamic, config, args.transport)

    try:
        transport = Transport(args.transport, args.endpoint, os.environ.get(args.api_key_env))
    except ValueError as exc:
        parser.error(str(exc))

    out_dir.mkdir(parents=True, exist_ok=True)
    cache = {}
    try:
        for architecture in architectures:
            for case in cases:
                environment_step = make_environment_step(
                    hidden_cases[case["test_id"]], case["payload"], shared)
                trajectory = run_episode(case, architecture, config, shared, transport,
                                         args.model, environment_step, cache, args.mock)
                path = out_dir / f'{case["test_id"]}_{architecture}_{trajectory["run_id"]}.json'
                path.write_text(json.dumps(trajectory, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
                print(f'{path}: {trajectory["outcome"]}')
    finally:
        transport.close()


if __name__ == "__main__":
    main()
