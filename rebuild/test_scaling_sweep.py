from rebuild.tools import scaling_sweep


def _row(runes, letters, windows, cpu):
    return {"runes": runes, "letters": letters, "windows": windows, "cpu": cpu}


def test_report_prints_na_for_a_pair_with_the_same_rune_count(capsys):
    scaling_sweep.report([_row(40, 30, 1000, 10.0), _row(41, 31, 1200, 12.0), _row(41, 31, 1200, 12.5)])
    lines = capsys.readouterr().out.splitlines()
    assert "41->41   windows   n/a   cpu   n/a" in lines
    assert any(line.startswith("whole-ladder fit over 3 rungs") for line in lines)
