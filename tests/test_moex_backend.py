"""Изменения цен: чтение списка тикеров и выбор референса для Δ10."""
from datetime import datetime, timedelta

import moex_backend as mb


class TestReadTickers:
    def test_default_file(self, monkeypatch, tmp_path):
        f = tmp_path / "poll_tickers.txt"
        f.write_text("SBER\nGAZP\n# комментарий\n\nT\n", encoding="utf-8")
        monkeypatch.setattr(mb, "POLL_FILE", f)
        assert mb._read_tickers() == ["SBER", "GAZP", "T"]

    def test_inline_comments_and_case(self, monkeypatch, tmp_path):
        f = tmp_path / "poll_tickers.txt"
        f.write_text("sber # банк\n LKOH\n", encoding="utf-8")
        monkeypatch.setattr(mb, "POLL_FILE", f)
        assert mb._read_tickers() == ["SBER", "LKOH"]

    def test_missing_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mb, "POLL_FILE", tmp_path / "нет_файла")
        assert mb._read_tickers() == []


class TestPickC10Ref:
    now = datetime(2026, 11, 13, 12, 30)

    def _row(self, minutes_ago, close):
        return (self.now - timedelta(minutes=minutes_ago), close, close)

    def test_close_at_cutoff(self):
        # Свеча, начавшаяся ровно 10 минут назад — референс.
        rows = [self._row(11, 100.0), self._row(10, 101.0), self._row(5, 102.0)]
        assert mb._pick_c10_ref(rows, self.now - timedelta(minutes=10)) == 101.0

    def test_skips_zero_closes(self):
        # Свечи с close=0 (нет сделок) не референс.
        rows = [self._row(12, 100.0), self._row(11, 0), self._row(10, 101.0),
                self._row(5, 102.0)]
        assert mb._pick_c10_ref(rows, self.now - timedelta(minutes=10)) == 101.0

    def test_day_started_recently_first_candle(self):
        # День начался <10 минут назад — берём первую свечу.
        rows = [self._row(8, 100.0), self._row(7, 101.0)]
        assert mb._pick_c10_ref(rows, self.now - timedelta(minutes=10)) == 100.0

    def test_no_candles(self):
        assert mb._pick_c10_ref([], self.now - timedelta(minutes=10)) is None
        assert mb._pick_c10_ref([self._row(5, 0)], self.now) is None
