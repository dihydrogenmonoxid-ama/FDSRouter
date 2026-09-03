from pathlib import Path

from fdsrouter.core.case_files import (
    create_case_dir,
    result_files,
    safe_filename,
    write_results_zip,
)


def _write_case(directory: Path, chid: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    fds = directory / f"{chid}.fds"
    fds.write_text(f"&HEAD CHID='{chid}' /\n&MESH IJK=10,10,10, XB=0,1,0,1,0,1 /\n", encoding="utf-8")
    return fds


def test_safe_filename_strips_path_traversal_in_both_separator_styles():
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename("..\\..\\windows\\evil.fds") == "evil.fds"
    assert safe_filename("/absolute/case.fds") == "case.fds"


def test_safe_filename_never_produces_a_dotfile_or_an_empty_name():
    assert safe_filename(".bashrc") == "bashrc"
    assert safe_filename("..") == "upload"
    assert safe_filename("") == "upload"


def test_create_case_dir_does_not_collide_within_the_same_second(tmp_path):
    first = create_case_dir(tmp_path, "atrium.fds")
    second = create_case_dir(tmp_path, "atrium.fds")

    assert first != second
    assert first.is_dir() and second.is_dir()


def test_result_files_collects_input_and_chid_outputs(tmp_path):
    fds = _write_case(tmp_path / "case", "atrium")
    for name in ("atrium.out", "atrium_hrr.csv", "atrium_devc.csv", "atrium_1_1.s3d"):
        (fds.parent / name).write_text("x", encoding="utf-8")

    names = {f.name for f in result_files(fds)}

    assert names == {"atrium.fds", "atrium.out", "atrium_hrr.csv", "atrium_devc.csv", "atrium_1_1.s3d"}


def test_result_files_ignores_other_cases_in_the_same_directory(tmp_path):
    case_dir = tmp_path / "shared"
    fds = _write_case(case_dir, "atrium")
    (case_dir / "atrium.out").write_text("x", encoding="utf-8")
    # A second case living next to it must not be packaged into this one's download.
    _write_case(case_dir, "tiefgarage")
    (case_dir / "tiefgarage.out").write_text("x", encoding="utf-8")

    names = {f.name for f in result_files(fds)}

    assert names == {"atrium.fds", "atrium.out"}


def test_result_files_of_a_missing_directory_is_empty(tmp_path):
    assert result_files(tmp_path / "gone" / "case.fds") == []


def test_write_results_zip_contains_the_case_files_flat(tmp_path):
    import zipfile

    fds = _write_case(tmp_path / "case", "atrium")
    (fds.parent / "atrium.out").write_text("STOP: FDS completed successfully\n", encoding="utf-8")

    archive = tmp_path / "out.zip"
    count = write_results_zip(fds, archive)

    assert count == 2
    with zipfile.ZipFile(archive) as zf:
        assert sorted(zf.namelist()) == ["atrium.fds", "atrium.out"]
        assert "completed successfully" in zf.read("atrium.out").decode()
