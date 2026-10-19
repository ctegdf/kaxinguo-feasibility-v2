"""Two fixed demos, served as explicitly marked MOCK chat completions.

Exactly one BT demo and one STEP demo are configured at startup. Requests are
routed to the right demo by the shape of the public dynamic_input (BT carries
Task+Robot_State, STEP carries events+runtime) and confirmed by task_id; no
out-of-band header is needed because only two fixtures are ever served.
"""
import argparse
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "数据集及参考计划"


def read_json(name):
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


class MockServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, bt_case, step_case, plan, step_trace):
        self.bt_case = bt_case
        self.step_case = step_case
        self.plan = plan
        self.step_trace = step_trace
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, body):
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def reject(self, message, status=400):
        self.send_json(status, {"error": {"message": message},
                                "mock_notice": "MOCK: fixed reference demo only"})

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.reject("Only POST /v1/chat/completions is supported", 404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8 * 1024 * 1024:
                raise ValueError("Missing or excessive Content-Length")
            body = json.loads(self.rfile.read(length))
            dynamic = json.loads(body["messages"][1]["content"])
            if not isinstance(dynamic, dict):
                raise ValueError("dynamic_input must be an object")
            is_bt = "Task" in dynamic and "Robot_State" in dynamic
            is_step = "events" in dynamic and "runtime" in dynamic
            if is_bt == is_step:
                raise ValueError("Unrecognized or ambiguous dynamic_input")
            case = self.server.bt_case if is_bt else self.server.step_case
            task_id = dynamic["Task"]["task_id"] if is_bt else dynamic["task_id"]
            if task_id != case["task_id"]:
                raise ValueError(f'Only configured demo {case["test_id"]} is supported')
            version = case["payload"]["protocol_version"]
            if is_bt:
                if dynamic != case["payload"]:
                    raise ValueError("BT input differs from the configured public input")
                output = {"protocol_version": version, "task_id": task_id,
                          "decision": "PLAN",
                          "behavior_tree": deepcopy(self.server.plan["behavior_tree"])}
            else:
                events = dynamic["events"]
                if not isinstance(events, list):
                    raise ValueError("events must be an array")
                index = len(events)
                if index >= len(self.server.step_trace):
                    raise ValueError("STEP round exceeds reference_trace length")
                revision = dynamic.get("context_revision")
                # Reject bool explicitly: True == 1 would otherwise slip through.
                if type(revision) is not int or revision != index + 1:
                    raise ValueError("context_revision does not match completed events")
                output = {"protocol_version": version, "task_id": task_id,
                          "context_revision": dynamic["context_revision"],
                          **deepcopy(self.server.step_trace[index]["decision"])}
            # structured_outputs and extra_body are intentionally ignored.
            self.send_json(200, {
                "id": "mock-" + str(uuid4()), "object": "chat.completion",
                "created": int(time.time()), "model": body.get("model", "mock"),
                "choices": [{"index": 0, "message": {
                    "role": "assistant", "content": json.dumps(output, ensure_ascii=False)},
                    "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "mock_notice": "MOCK: hand-authored reference; no model inference",
            })
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            self.reject(str(exc))

    def do_GET(self):
        if self.path == "/healthz":
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.reject("Only POST /v1/chat/completions is supported", 405)

    def log_message(self, format, *args):
        print("[MOCK] " + format % args, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8800)
    parser.add_argument("--bt-test-id", required=True)
    parser.add_argument("--step-test-id", required=True)
    args = parser.parse_args()
    public = {c["test_id"]: c for c in read_json("test_inputs.json")["cases"]}
    hidden = {c["test_id"]: c for c in read_json("validation_data.json")["cases"]}
    plans = {p["task_id"]: p for p in read_json("reference_plans.json")["plans"]}
    if args.bt_test_id not in public or args.step_test_id not in public:
        parser.error("Both demo IDs must exist in test_inputs.json")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    bt_case, step_case = public[args.bt_test_id], public[args.step_test_id]
    server = MockServer(("127.0.0.1", args.port), bt_case, step_case,
                        plans[bt_case["task_id"]], hidden[args.step_test_id]["reference_trace"])
    print(f"[MOCK] serving http://127.0.0.1:{args.port}/v1/chat/completions "
          f"bt={args.bt_test_id} step={args.step_test_id}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
