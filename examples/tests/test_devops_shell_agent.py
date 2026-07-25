from __future__ import annotations

from products.devops_shell_agent import DevOpsShellAgent


def test_clean_output_is_returned_unchanged():
    agent = DevOpsShellAgent()
    output = agent.run_command("tail -n 20 /var/log/app.log", "INFO request handled in 42ms")

    assert output == "INFO request handled in 42ms"
    assert agent.calls_with_redaction() == []


def test_leaked_api_key_in_output_is_redacted():
    agent = DevOpsShellAgent()
    output = agent.run_command("cat config.env", 'api_key: "sk-abcdefghijklmnopqrstuvwxyz123456"')

    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in output


def test_leaked_database_url_in_output_is_redacted():
    agent = DevOpsShellAgent()
    output = agent.run_command("cat config.env", "DATABASE_URL=postgres://user:pass@host/db")

    assert "user:pass@host" not in output


def test_calls_with_redaction_only_includes_triggered_calls():
    agent = DevOpsShellAgent()
    agent.run_command("git log --oneline -5", "a1b2c3d fix: resolve login timeout")
    agent.run_command("cat config.env", 'api_key: "sk-abcdefghijklmnopqrstuvwxyz123456"')

    flagged = agent.calls_with_redaction()

    assert len(flagged) == 1
    assert flagged[0].input_summary["command"] == "cat config.env"


def test_every_command_is_recorded_in_audit_log_regardless_of_redaction():
    agent = DevOpsShellAgent()
    agent.run_command("echo hello", "hello")
    agent.run_command("cat config.env", 'api_key: "sk-abcdefghijklmnopqrstuvwxyz123456"')

    assert len(agent.audit_log.all()) == 2
