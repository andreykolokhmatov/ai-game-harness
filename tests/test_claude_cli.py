import json
import os
import sys
import uuid
from pathlib import Path

import pytest

from harness.runners.base import AgentRequest
from harness.runners.claude_cli import ClaudeCliRunner, build_command
from harness.runners.mock import MockResponse, MockRunner

FAKE = [sys.executable, str(Path(__file__).with_name("fake_claude.py"))]


def make_req(tmp_path: Path, **kw) -> AgentRequest:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    env = dict(os.environ)
    env.update(kw.pop("env", {}))
    defaults = dict(
        run_id="r-0001",
        role="engineer",
        prompt="сделай игру",
        model="claude-sonnet-5-5",
        cwd=repo,
        transcript_path=tmp_path / "runs" / "r-0001" / "transcript.jsonl",
        session_id=str(uuid.uuid4()),
        timeout_s=30,
        env=env,
    )
    defaults.update(kw)
    return AgentRequest(**defaults)


def test_build_command_flags(tmp_path):
    req = make_req(
        tmp_path,
        effort="medium",
        tools=["Read", "Bash"],
        allowed_tools=["Bash(git status *)"],
        disallowed_tools=["Edit(.claude/**)"],
        json_schema={"type": "object"},
        max_turns=5,
        fallback_models=["claude-sonnet-5-5"],
    )
    cmd = build_command(req, ["claude"])
    assert cmd[:2] == ["claude", "-p"]
    assert cmd[cmd.index("--setting-sources") + 1] == ""
    assert cmd[cmd.index("--session-id") + 1] == req.session_id
    assert cmd[cmd.index("--tools") + 1] == "Read,Bash"
    settings = json.loads(cmd[cmd.index("--settings") + 1])
    assert settings["permissions"] == {"allow": ["Bash(git status *)"], "deny": ["Edit(.claude/**)"]}
    assert "--strict-mcp-config" in cmd
    assert "сделай игру" not in cmd  # prompt goes through stdin


def test_build_command_resume(tmp_path):
    cmd = build_command(make_req(tmp_path, resume_session_id="abc"), ["claude"])
    assert cmd[cmd.index("--resume") + 1] == "abc"
    assert "--session-id" not in cmd


def test_ok_run_parses_result_and_writes_transcript(tmp_path):
    argv_out = tmp_path / "argv.json"
    req = make_req(tmp_path, env={"FAKE_CLAUDE_MODE": "ok", "FAKE_CLAUDE_ARGV_OUT": str(argv_out)})
    result = ClaudeCliRunner(FAKE).run(req)
    assert result.status == "ok"
    assert result.session_id == req.session_id
    assert result.structured_output == {"ok": True}
    assert result.num_turns == 4
    assert result.cost_usd_estimate == 0.25
    assert result.usage_by_model["claude-sonnet-5-5"]["outputTokens"] == 7
    assert json.loads(argv_out.read_text(encoding="utf-8"))["prompt"] == "сделай игру"
    lines = req.transcript_path.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[-1])["type"] == "result"


def test_max_turns(tmp_path):
    result = ClaudeCliRunner(FAKE).run(make_req(tmp_path, env={"FAKE_CLAUDE_MODE": "max_turns"}))
    assert result.status == "max_turns"


def test_crash_without_result_uses_fallback_usage(tmp_path):
    result = ClaudeCliRunner(FAKE).run(make_req(tmp_path, env={"FAKE_CLAUDE_MODE": "crash"}))
    assert result.status == "agent_error"
    assert result.exit_code == 1
    assert "boom" in (result.error or "")
    assert result.usage_by_model["claude-sonnet-5-5"]["outputTokens"] == 7


def test_rate_limit_is_usage_limit(tmp_path):
    result = ClaudeCliRunner(FAKE).run(make_req(tmp_path, env={"FAKE_CLAUDE_MODE": "rate_limited"}))
    assert result.status == "usage_limit"


@pytest.mark.skipif(sys.platform == "win32", reason="SIGINT delivery to the fake differs on Windows")
def test_timeout_interrupts_and_reports_timeout(tmp_path):
    result = ClaudeCliRunner(FAKE).run(make_req(tmp_path, env={"FAKE_CLAUDE_MODE": "hang"}, timeout_s=2))
    assert result.status == "timeout"
    assert result.duration_s < 15


@pytest.mark.skipif(sys.platform == "win32", reason="SIGINT delivery to the fake differs on Windows")
def test_idle_timeout(tmp_path):
    req = make_req(tmp_path, env={"FAKE_CLAUDE_MODE": "hang"}, timeout_s=60, idle_timeout_s=2)
    result = ClaudeCliRunner(FAKE).run(req)
    assert result.status == "timeout"
    assert result.duration_s < 30


def test_mock_runner_writes_files(tmp_path):
    runner = MockRunner([MockResponse(files={"scenes/main.tscn": "[gd_scene]"}, structured_output={"a": 1})])
    req = make_req(tmp_path)
    result = runner.run(req)
    assert result.status == "ok" and result.structured_output == {"a": 1}
    assert (req.cwd / "scenes" / "main.tscn").read_text(encoding="utf-8") == "[gd_scene]"
    with pytest.raises(AssertionError):
        runner.run(req)
