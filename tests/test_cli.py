import json

from wearables import cli


def run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_summary_table(capsys, export_xml):
    code, out, _ = run(capsys, "summary", str(export_xml))
    assert code == 0
    assert "running" in out and "resting HR" in out and "sleep:" in out


def test_summary_json_is_valid(capsys, export_xml):
    code, out, _ = run(capsys, "summary", str(export_xml), "--json")
    data = json.loads(out)
    assert code == 0 and data["workouts"] > 0 and data["resting_hr"]["recent"] > 0


def test_weekly_limits_rows(capsys, export_xml):
    code, out, _ = run(capsys, "weekly", str(export_xml), "--weeks", "3")
    assert code == 0
    assert len(out.strip().splitlines()) == 2 + 3  # header, rule, rows


def test_load_with_explicit_heart_rates(capsys, export_xml):
    code, out, _ = run(capsys, "load", str(export_xml), "--rest-hr", "55", "--max-hr", "190")
    assert code == 0 and "acute:chronic ratio" in out and "not medical advice" in out


def test_errors_exit_nonzero(capsys, tmp_path):
    code, _, err = run(capsys, "summary", str(tmp_path / "missing.xml"))
    assert code == 1 and err.startswith("error:")


def test_synth_command_writes_parseable_export(capsys, tmp_path):
    out_file = tmp_path / "demo.xml"
    code, out, _ = run(capsys, "synth", str(out_file), "--days", "30", "--seed", "1", "--end", "2026-10-31")
    assert code == 0 and "synthetic" in out
    code, out, _ = run(capsys, "weekly", str(out_file))
    assert code == 0 and "2026-" in out
