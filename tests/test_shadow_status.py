"""The README's shadow-mode table must follow the PRD §8 exit rule and the shadow log."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("shadow_status", ROOT / "shadow/status.py")
status = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(status)

EXIT_AT = status.schedule((ROOT / "PRD.md").read_text())


def _log(n, misses_at=(), run=1, checker="0.2.0"):
    return [{"run": run, "checker": checker, "case": str(i), "miss": i in misses_at} for i in range(1, n + 1)]


def test_schedule_matches_prd():
    assert len(EXIT_AT) == 4 and EXIT_AT == sorted(EXIT_AT)


def test_clean_run_passes_exactly_at_first_count():
    (r,) = status.runs(_log(EXIT_AT[0] - 1), EXIT_AT)
    assert r["status"] is None
    (r,) = status.runs(_log(EXIT_AT[0]), EXIT_AT)
    assert r["status"] == "passed"


def test_a_miss_moves_the_exit_instead_of_restarting():
    (r,) = status.runs(_log(EXIT_AT[0], misses_at={10}), EXIT_AT)
    assert (r["n"], r["misses"], r["status"]) == (EXIT_AT[0], 1, None)
    (r,) = status.runs(_log(EXIT_AT[1], misses_at={10}), EXIT_AT)
    assert r["status"] == "passed"


def test_too_many_misses_fails_the_run():
    (r,) = status.runs(_log(4, misses_at={1, 2, 3, 4}), EXIT_AT)
    assert r["status"] == "failed" and r["n"] == 4


def test_log_cannot_continue_an_ended_run_or_swap_checkers():
    with pytest.raises(ValueError, match="already ended"):
        status.runs(_log(EXIT_AT[0] + 1), EXIT_AT)
    with pytest.raises(ValueError, match="new run"):
        status.runs(_log(3) + _log(1, checker="0.3.0"), EXIT_AT)


def test_failed_run_stays_next_to_the_new_one():
    rs = status.runs(_log(4, misses_at={1, 2, 3, 4}) + _log(5, run=2, checker="0.3.0"), EXIT_AT)
    text = status.table(rs, EXIT_AT)
    assert "| 1 | 0.2.0 | 4 | 4 | – | Failed" in text and "| 2 | 0.3.0 | 5 | 0 |" in text


def test_readme_table_is_up_to_date():
    old, new = status.render()
    assert old == new, "run python shadow/status.py"
