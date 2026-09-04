"""determine_run_status: the shared "was this run actually good?" decision used by both the
local dispatcher and a remote agent (extracted so they can never drift apart on it)."""

from fdsrouter.core.job_runner import determine_run_status

COMPLETED_OUT = "some FDS banner text\n...\nSTOP: FDS completed successfully\n"


def test_stop_requested_always_wins_as_cancelled(tmp_path):
    out_path = tmp_path / "case.out"
    out_path.write_text(COMPLETED_OUT)

    status, message = determine_run_status(out_path, [], stop_requested=True, return_code=0)

    assert status == "cancelled"
    assert message == "durch Nutzer beendet"


def test_completed_out_file_with_no_errors_is_done(tmp_path):
    out_path = tmp_path / "case.out"
    out_path.write_text(COMPLETED_OUT)

    status, message = determine_run_status(out_path, ["normal line"], stop_requested=False, return_code=0)

    assert status == "done"
    assert message is None


def test_zero_exit_code_is_not_trusted_when_out_reports_error(tmp_path):
    out_path = tmp_path / "case.out"
    out_path.write_text(COMPLETED_OUT)

    status, message = determine_run_status(
        out_path, ["ERROR(314): SURF BURNER Must have a REAC line"], stop_requested=False, return_code=0
    )

    assert status == "failed"
    assert "ERROR" in message


def test_missing_out_file_is_failed(tmp_path):
    out_path = tmp_path / "does-not-exist.out"

    status, message = determine_run_status(out_path, [], stop_requested=False, return_code=1)

    assert status == "failed"
    assert message == "exit code 1"


def test_incomplete_out_file_is_failed(tmp_path):
    out_path = tmp_path / "case.out"
    out_path.write_text("some FDS banner text\n...still running when the process died\n")

    status, message = determine_run_status(out_path, [], stop_requested=False, return_code=-9)

    assert status == "failed"
    assert message == "exit code -9"


def test_failure_message_falls_back_to_exit_code_when_log_is_empty(tmp_path):
    out_path = tmp_path / "does-not-exist.out"

    status, message = determine_run_status(out_path, ["   ", ""], stop_requested=False, return_code=127)

    assert status == "failed"
    assert message == "exit code 127"


def test_failure_message_uses_the_last_five_log_lines(tmp_path):
    out_path = tmp_path / "does-not-exist.out"
    lines = [f"line {i}" for i in range(20)]

    status, message = determine_run_status(out_path, lines, stop_requested=False, return_code=1)

    assert status == "failed"
    assert message == "\n".join(lines[-5:])
