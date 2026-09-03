from fdsrouter.core.fds_parser import (
    parse_mesh_cell_count,
    parse_mesh_count,
    parse_sim_end_time_s,
)


def test_single_mesh():
    text = "&MESH IJK=36,24,24, XB=0.0,3.6,0.0,2.4,0.0,2.4 /\n"
    assert parse_mesh_cell_count(text) == 36 * 24 * 24


def test_multiple_meshes_are_summed():
    text = (
        "&MESH IJK=10,10,10, XB=0,1,0,1,0,1 /\n"
        "&MESH IJK=5,5,5, XB=1,2,0,1,0,1 /\n"
    )
    assert parse_mesh_cell_count(text) == 10 * 10 * 10 + 5 * 5 * 5


def test_commented_out_mesh_is_ignored():
    text = "! &MESH IJK=10,10,10, XB=0,1,0,1,0,1 /\n&MESH IJK=2,2,2, XB=0,1,0,1,0,1 /\n"
    assert parse_mesh_cell_count(text) == 8


def test_mesh_with_id_before_ijk():
    text = "&MESH ID='Domain' IJK=10,10,10 XB=-2.0,2.0,-2.0,2.0,0.0,4.0 /\n"
    assert parse_mesh_cell_count(text) == 1000


def test_no_mesh_returns_zero():
    assert parse_mesh_cell_count("&HEAD CHID='x' /\n") == 0


def test_parses_t_end():
    assert parse_sim_end_time_s("&TIME T_END=60.0 /\n") == 60.0


def test_missing_t_end_returns_none():
    assert parse_sim_end_time_s("&HEAD CHID='x' /\n") is None


def test_parse_mesh_count_counts_namelists_not_cells():
    text = (
        "&MESH IJK=10,10,10, XB=0,1,0,1,0,1 /\n"
        "&MESH IJK=5,5,5, XB=1,2,0,1,0,1 /\n"
        "&MESH IJK=2,2,2, XB=2,3,0,1,0,1 /\n"
    )
    assert parse_mesh_count(text) == 3


def test_parse_mesh_count_zero_when_no_mesh():
    assert parse_mesh_count("&HEAD CHID='x' /\n") == 0


