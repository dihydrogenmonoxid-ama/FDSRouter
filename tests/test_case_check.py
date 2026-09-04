"""The pre-flight check: every finding stands for a way a run is lost minutes after it starts."""

from pathlib import Path

from fdsrouter.core.case_check import check_case_file, check_case_text

VALID = """
&HEAD CHID='atrium' /
&MESH IJK=10,10,10, XB=0,1,0,1,0,1 /
&TIME T_END=60.0 /
&DUMP DT_HRR=1.0 /
&REAC FUEL='PROPANE' /
&SURF ID='BURNER', HRRPUA=400.0 /
&VENT XB=0.4,0.8,0.4,0.8,0.0,0.0, SURF_ID='BURNER' /
&TAIL /
"""


def codes(text, case_dir=None):
    return [f.code for f in check_case_text(text, case_dir)]


def test_a_complete_case_has_nothing_to_report():
    assert codes(VALID) == []


def test_hrrpua_without_a_reaction_is_the_error_fds_aborts_with():
    # ERROR(314) in FDS: "SURF ... Must have a REAC line when using HRRPUA".
    text = VALID.replace("&REAC FUEL='PROPANE' /\n", "")

    assert "hrrpua_without_reac" in codes(text)


def test_a_case_without_a_mesh_is_rejected():
    assert "no_mesh" in codes("&HEAD CHID='x' /\n&TIME T_END=1 /\n")


def test_a_surface_referenced_but_never_defined_is_found():
    text = VALID.replace("SURF_ID='BURNER'", "SURF_ID='BRENNER'")

    findings = [f for f in check_case_text(text) if f.code == "missing_surf"]

    assert len(findings) == 1
    assert findings[0].detail == "BRENNER"


def test_fds_own_surfaces_are_not_reported_as_missing():
    text = VALID + "&VENT XB=0,1,0,1,1,1, SURF_ID='OPEN' /\n"

    assert "missing_surf" not in codes(text)


def test_a_ramp_referenced_but_never_defined_is_found():
    text = VALID.replace("HRRPUA=400.0", "HRRPUA=400.0, RAMP_Q='brandverlauf'")

    findings = [f for f in check_case_text(text) if f.code == "missing_ramp"]

    assert [f.detail for f in findings] == ["BRANDVERLAUF"]


def test_a_defined_ramp_is_accepted():
    text = VALID.replace("HRRPUA=400.0", "HRRPUA=400.0, RAMP_Q='verlauf'") + (
        "&RAMP ID='verlauf', T=0, F=0 /\n&RAMP ID='verlauf', T=60, F=1 /\n"
    )

    assert "missing_ramp" not in codes(text)


def test_a_referenced_file_next_to_the_case_is_accepted(tmp_path):
    (tmp_path / "geometrie.fds").write_text("&TAIL /", encoding="utf-8")
    text = VALID + "&CATF OTHER_FILES='geometrie.fds' /\n"

    assert "missing_file" not in codes(text, tmp_path)


def test_a_referenced_file_that_is_not_there_is_reported(tmp_path):
    text = VALID + "&CATF OTHER_FILES='geometrie.fds' /\n"

    findings = [f for f in check_case_text(text, tmp_path) if f.code == "missing_file"]

    assert [f.detail for f in findings] == ["geometrie.fds"]


def test_a_missing_chid_is_a_warning_because_the_results_download_needs_it():
    text = VALID.replace("&HEAD CHID='atrium' /\n", "")

    assert "no_chid" in codes(text)


def test_a_missing_end_time_is_a_warning():
    text = VALID.replace("&TIME T_END=60.0 /\n", "")

    assert "no_t_end" in codes(text)


def test_multi_line_namelists_are_read_like_fds_reads_them():
    text = """
&HEAD CHID='atrium' /
&MESH IJK=10,10,10,
      XB=0,1,0,1,0,1 /
&TIME T_END=60.0 /
&DUMP DT_HRR=1.0 /
&REAC FUEL='PROPANE' /
&SURF ID='BURNER',
      HRRPUA=400.0 /
&VENT XB=0.4,0.8,0.4,0.8,0.0,0.0,
      SURF_ID='BURNER' /
"""

    assert codes(text) == []


def test_the_real_case_file_that_failed_in_testing(tmp_path):
    case = tmp_path / "livecheck.fds"
    case.write_text(VALID.replace("&REAC FUEL='PROPANE' /\n", ""), encoding="utf-8")

    assert "hrrpua_without_reac" in [f.code for f in check_case_file(case)]
