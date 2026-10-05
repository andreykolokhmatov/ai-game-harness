"""Stand-in for the `claude` CLI in tests. Behaviour is chosen by FAKE_CLAUDE_MODE.

Modes: ok, max_turns, hang (ignores nothing; exits on SIGINT with a result like the
real CLI), crash (assistant message, no result), rate_limited.
"""

import json
import os
import signal
import sys
import time


def emit(event):
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


def arg(name, default=None):
    argv = sys.argv
    return argv[argv.index(name) + 1] if name in argv else default


def main():
    mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
    session = arg("--session-id") or arg("--resume")
    prompt = sys.stdin.read()
    if os.environ.get("FAKE_CLAUDE_ARGV_OUT"):
        with open(os.environ["FAKE_CLAUDE_ARGV_OUT"], "w", encoding="utf-8") as f:
            json.dump({"argv": sys.argv[1:], "prompt": prompt}, f)

    emit({"type": "system", "subtype": "init", "session_id": session, "model": arg("--model"), "tools": []})
    usage = {"input_tokens": 3, "output_tokens": 7, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 0}
    emit({"type": "assistant", "message": {"id": "msg_1", "model": arg("--model"), "usage": usage, "content": []}})

    if mode == "hang":
        def on_int(*_):
            emit({"type": "result", "subtype": "error_during_execution", "is_error": True, "session_id": session,
                  "num_turns": 1, "total_cost_usd": 0, "modelUsage": {}})
            sys.exit(0)

        signal.signal(signal.SIGINT, on_int)
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, on_int)
        time.sleep(120)
        return
    if mode == "crash":
        sys.stderr.write("boom\n")
        sys.exit(1)
    if mode == "rate_limited":
        emit({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": 1791234600,
                                                             "rateLimitType": "five_hour"}})
        emit({"type": "result", "subtype": "error_during_execution", "is_error": True, "session_id": session,
              "num_turns": 1, "total_cost_usd": 0.01, "modelUsage": {}})
        return

    subtype = "error_max_turns" if mode == "max_turns" else "success"
    emit({
        "type": "result", "subtype": subtype, "is_error": mode != "ok", "session_id": session, "num_turns": 4,
        "result": "done", "total_cost_usd": 0.25, "structured_output": {"ok": True} if mode == "ok" else None,
        "modelUsage": {arg("--model"): {"inputTokens": 3, "outputTokens": 7, "costUSD": 0.25}},
        "permission_denials": [],
    })


main()
