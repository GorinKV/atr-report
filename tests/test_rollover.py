"""Авторолловер фьючерсов: разбор файла, замена строки, выбор контракта."""
import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location(
    "futures_rollover", Path(__file__).resolve().parent.parent / "futures_rollover.py")
assert spec is not None and spec.loader is not None, "spec не найден"
fr = importlib.util.module_from_spec(spec)
sys.modules["futures_rollover"] = fr
spec.loader.exec_module(fr)


def _inst(ticker, asset, size, exp_days, name=None):
    exp = datetime.now(UTC) + timedelta(days=exp_days)
    return SimpleNamespace(ticker=ticker, basic_asset=asset,
                           basic_asset_size=size, expiration_date=exp,
                           name=name or ticker, figi=f"F{ticker}")


NOW = datetime.now(UTC)


class TestParse:
    def test_tickers_and_comments(self):
        lines = ["# комментарий", "", "BRZ6     # нефть", "# ещё", "NGZ6 # газ"]
        assert fr._parse(lines) == [(2, "BRZ6"), (4, "NGZ6")]

    def test_empty(self):
        assert fr._parse(["# только комментарии"]) == []


class TestPickReplacement:
    def test_no_roll_when_far(self):
        # до экспирации много дней — роллить не надо
        old = _inst("BRZ6", "Brent", 10, 30)
        nxt = _inst("BRF7", "Brent", 10, 90)
        res = fr._pick_replacement("BRZ6", old, {"BRZ6": old, "BRF7": nxt},
                                   NOW, roll_before_days=7, min_runway_days=14)
        assert res is None

    def test_rolls_to_same_asset_same_size(self):
        old = _inst("BRZ6", "Brent", 10, 2)
        nxt = _inst("BRF7", "Brent", 10, 90)
        mini = _inst("BMF7", "Brent", 1, 90)     # мини — не подходит
        other = _inst("XXX7", "Wheat", 10, 90)   # другой актив
        soon = _inst("BRH7", "Brent", 10, 5)     # сам скоро истечёт
        res = fr._pick_replacement(
            "BRZ6", old,
            {"BRZ6": old, "BRF7": nxt, "BMF7": mini, "XXX7": other,
             "BRH7": soon},
            NOW, roll_before_days=7, min_runway_days=14)
        assert res is not None
        assert res[0] == "BRF7"

    def test_chooses_most_liquid(self):
        old = _inst("CCX6", "Cocoa", 5, 3)
        a = _inst("CCG7", "Cocoa", 5, 90)
        b = _inst("CCJ7", "Cocoa", 5, 150)
        vols = {"FCCG7": 100, "FCCJ7": 9000}
        res = fr._pick_replacement(
            "CCX6", old, {"CCX6": old, "CCG7": a, "CCJ7": b},
            NOW, roll_before_days=7, min_runway_days=14,
            volume_of=lambda figi: vols.get(figi, 0))
        assert res is not None
        assert res[0] == "CCJ7"

    def test_no_candidates(self):
        old = _inst("BRZ6", "Brent", 10, 2)
        res = fr._pick_replacement("BRZ6", old, {"BRZ6": old}, NOW,
                                   roll_before_days=7, min_runway_days=14)
        assert res is None


class TestApplyRoll:
    LINES = ["# шапка", "", "BRZ6     # BR-12.26 до 02.12.2026",
             "NGZ6     # NG-12.26 до 30.12.2026"]

    def test_replaces_only_old(self):
        old_exp = datetime(2026, 12, 2, tzinfo=UTC)
        new_exp = datetime(2027, 1, 5, tzinfo=UTC)
        out = fr._apply_roll(self.LINES, "BRZ6", "BRF7", "BR-1.27 Нефть Brent",
                             new_exp, old_exp)
        assert out[0] == "# шапка"
        assert out[1] == ""
        assert out[2].startswith("BRF7") and "BRZ6" in out[2]
        assert "05.01.2027" in out[2]
        assert out[3] == self.LINES[3]  # остальные не тронуты

    def test_file_roundtrip(self, tmp_path):
        p = tmp_path / "poll_futures.txt"
        p.write_text("\n".join(self.LINES) + "\n", encoding="utf-8")
        assert fr._parse(fr._read_lines(p)) == [(2, "BRZ6"), (3, "NGZ6")]
        lines = fr._apply_roll(fr._read_lines(p), "BRZ6", "BRF7",
                               "BR-1.27", datetime(2027, 1, 5, tzinfo=UTC),
                               datetime(2026, 12, 2, tzinfo=UTC))
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        assert fr._parse(fr._read_lines(p)) == [(2, "BRF7"), (3, "NGZ6")]


class TestCheckOnce:
    def test_rolls_and_notifies(self, tmp_path):
        import alerts
        with alerts._lock:
            alerts._sent_at.clear()
        p = tmp_path / "f.txt"
        p.write_text("# шапка\n\nBRZ6     # нефть до 02.12.2026\n", encoding="utf-8")
        old = _inst("BRZ6", "Brent", 10, 2, "BR-12.26")
        nxt = _inst("BRF7", "Brent", 10, 90, "BR-1.27 Нефть Brent")
        rolled = fr.check_once(path=p, instruments={"BRZ6": old, "BRF7": nxt},
                               now=NOW, roll_before=7, min_runway=14)
        assert rolled == [("BRZ6", "BRF7")]
        txt = p.read_text(encoding="utf-8")
        assert "BRF7" in txt and "BRZ6" in txt and txt.startswith("# шапка")
        kinds = [a["kind"] for a in alerts.recent(0)]
        assert kinds[-1] == "roll"
        assert "BRF7" in alerts.recent(0)[-1]["text"]

    def test_no_roll_when_far(self, tmp_path):
        p = tmp_path / "f.txt"
        p.write_text("BRZ6     # нефть до 02.12.2026\n", encoding="utf-8")
        old = _inst("BRZ6", "Brent", 10, 30)
        nxt = _inst("BRF7", "Brent", 10, 90)
        assert fr.check_once(path=p, instruments={"BRZ6": old, "BRF7": nxt},
                             now=NOW) == []
        assert "BRZ6" in p.read_text(encoding="utf-8")

    def test_unknown_ticker_skipped(self, tmp_path):
        p = tmp_path / "f.txt"
        p.write_text("ZZZ9\n", encoding="utf-8")
        assert fr.check_once(path=p, instruments={}) == []
