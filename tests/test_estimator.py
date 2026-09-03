from fdsrouter.core.estimator import estimate_duration_s


def test_no_cell_count_is_unknown():
    est = estimate_duration_s(None, 4, [])
    assert est.basis == "unknown"
    assert est.seconds is None


def test_no_history_uses_fallback():
    est = estimate_duration_s(20000, 1, [])
    assert est.basis == "fallback"
    assert est.seconds is not None
    assert est.seconds > 0


def test_uses_similar_history_and_scales_by_cores():
    history = [
        {"mesh_cell_count": 20000, "mpi_process_count": 2, "actual_duration_s": 200.0},
        {"mesh_cell_count": 22000, "mpi_process_count": 2, "actual_duration_s": 220.0},
    ]
    # same cell count, double the cores -> roughly half the duration
    est = estimate_duration_s(20000, 4, history)
    assert est.basis == "history"
    assert est.sample_size == 2
    assert 90 < est.seconds < 110


def test_dissimilar_history_is_excluded():
    history = [{"mesh_cell_count": 20_000_000, "mpi_process_count": 1, "actual_duration_s": 500.0}]
    est = estimate_duration_s(1000, 1, history)
    assert est.basis == "fallback"


def test_history_scales_linearly_with_cell_count():
    history = [{"mesh_cell_count": 10000, "mpi_process_count": 1, "actual_duration_s": 100.0}]
    est = estimate_duration_s(20000, 1, history)
    assert est.basis == "history"
    assert est.seconds == 200.0
