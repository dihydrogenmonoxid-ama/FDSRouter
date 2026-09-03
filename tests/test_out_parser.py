from fdsrouter.core.out_parser import parse_devc_latest, parse_latest_hrr_kw, parse_out_file, parse_out_text


def test_parses_real_out_file(fixtures_dir):
    status = parse_out_file(fixtures_dir / "simple_test.out")
    assert status is not None
    assert status.step_number == 111
    assert status.simulation_time_s == 2.0
    assert status.step_size_s == 0.736e-02
    assert status.completed_successfully is True
    assert status.limiting_mesh == 1  # single-mesh case -- still reported, trivially Mesh 1


def test_parses_limiting_mesh_from_real_two_mesh_out_file(fixtures_dir):
    status = parse_out_file(fixtures_dir / "mm_test.out")
    assert status is not None
    assert status.completed_successfully is True
    assert status.limiting_mesh == 1  # the real run's final step reported Mesh 1 as limiting


def test_missing_file_returns_none(tmp_path):
    assert parse_out_file(tmp_path / "does_not_exist.out") is None


def test_mid_run_out_has_no_completion_marker():
    partial = (
        "       Time Step        5   September  3, 2026  15:16:59\n"
        "       Step Size:  0.442E-02 s, Total Time:    0.41668 s\n"
        "       Pressure Iterations: 4\n"
    )
    status = parse_out_text(partial)
    assert status.step_number == 5
    assert status.simulation_time_s == 0.41668
    assert status.completed_successfully is False


def test_counts_warnings_and_errors():
    text = (
        " *** Warning: something looked odd\n"
        " *** Error: something failed\n"
        "Warning: a second one\n"
    )
    status = parse_out_text(text)
    assert status.warnings_count == 3


def test_parses_latest_hrr_from_real_csv(fixtures_dir):
    hrr = parse_latest_hrr_kw(fixtures_dir / "simple_test_hrr.csv")
    assert hrr is not None
    assert hrr > 0


def test_hrr_missing_file_returns_none(tmp_path):
    assert parse_latest_hrr_kw(tmp_path / "missing_hrr.csv") is None


def test_parses_latest_devc_readings_from_real_csv(fixtures_dir):
    devices = parse_devc_latest(fixtures_dir / "devc_test_devc.csv")
    assert set(devices) == {"TC_1", "TC_2"}
    assert all(v > 0 for v in devices.values())


def test_devc_missing_file_returns_empty_dict(tmp_path):
    assert parse_devc_latest(tmp_path / "missing_devc.csv") == {}
