"""Сводные плотности: объединение соседних уровней (≤2 тика, ≥⅓ размера)."""
import density_store as ds

T = 0.01  # тик


def _r(ticker="SBER", side="bid", price=300.0, size=100000, **kw):
    r = {"ticker": ticker, "side": side, "price": price, "size": size,
         "eaten": 0, "eaten_pct": 0.0, "traded": 0, "traded_pct": 0.0,
         "hits": 0, "ratio": 2.0, "first_ts": "2026-11-17T10:00:00",
         "last_ts": "2026-11-17T10:00:00", "is_super": False,
         "active": True, "gone": False, "status": ""}
    r.update(kw)
    return r


def _tick_of(tk):
    return T


class TestMergeLevels:
    def test_no_merge_far(self):
        rows = [_r(price=300.00, size=200000), _r(price=300.10, size=100000)]
        out = ds.merge_levels(rows, _tick_of)
        assert len(out) == 2
        assert all(not r.get("merged") for r in out)

    def test_merge_within_two_ticks(self):
        rows = [_r(price=300.00, size=200000, status="держится, 15 ударов",
                   hits=15),
                _r(price=300.01, size=100000, hits=5)]
        out = ds.merge_levels(rows, _tick_of)
        assert len(out) == 1
        m = out[0]
        assert m["merged"] is True and m["parts"] == 2
        assert m["size"] == 300000
        assert m["hits"] == 20  # сумма ударов (15 + 5)
        assert abs(m["price"] - 300.003333) < 1e-5  # взвешенная по размеру
        assert m["price_lo"] == 300.0 and m["price_hi"] == 300.01
        assert "🧩" in m["status"] and "сводная из 2" in m["status"]
        assert "держится" in m["status"]  # статус крупного сохранён

    def test_boundary_two_ticks_exactly(self):
        rows = [_r(price=300.00, size=200000), _r(price=300.02, size=100000)]
        out = ds.merge_levels(rows, _tick_of)
        assert len(out) == 1  # ровно 2 тика — ряд

    def test_beyond_two_ticks_not_merged(self):
        rows = [_r(price=300.00, size=200000), _r(price=300.03, size=100000)]
        out = ds.merge_levels(rows, _tick_of)
        assert len(out) == 2

    def test_third_rule(self):
        # сосед ≥⅓ размера — сводится, меньше трети — нет
        big = _r(price=300.00, size=300000)
        ok = _r(price=300.01, size=100000)      # ровно 1/3 — да
        out = ds.merge_levels([big, ok], _tick_of)
        assert len(out) == 1
        small = _r(price=300.01, size=99999)    # чуть меньше 1/3 — нет
        out = ds.merge_levels([big, small], _tick_of)
        assert len(out) == 2

    def test_sides_and_tickers_separate(self):
        rows = [_r(side="bid", price=300.0), _r(side="ask", price=300.01),
                _r(ticker="VTBR", price=300.0)]
        out = ds.merge_levels(rows, _tick_of)
        assert len(out) == 3

    def test_sums_of_eaten_traded_super_active(self):
        rows = [
            _r(price=300.00, size=200000, eaten=100000, traded=150000,
               eaten_pct=50.0, traded_pct=75.0, is_super=True),
            _r(price=300.01, size=100000, eaten=40000, traded=60000,
               eaten_pct=40.0, traded_pct=60.0),
        ]
        out = ds.merge_levels(rows, _tick_of)
        m = out[0]
        assert m["eaten"] == 140000
        assert m["traded"] == 210000
        assert abs(m["eaten_pct"] - 46.7) < 0.05
        assert abs(m["traded_pct"] - 70.0) < 0.05
        assert m["is_super"] is True   # любой из частей супер
        assert m["active"] is True
        assert m["gone"] is False

    def test_gone_only_if_all_gone(self):
        rows = [_r(price=300.0, gone=True, active=False, status="ушла"),
                _r(price=300.01, gone=False, active=True)]
        out = ds.merge_levels(rows, _tick_of)
        assert out[0]["gone"] is False and out[0]["active"] is True

    def test_fallback_eps_without_tick(self):
        # тик неизвестен: eps = 0.01% цены × 2 = 0.06 при цене 300
        rows = [_r(price=300.0, size=200000), _r(price=300.05, size=100000)]
        out = ds.merge_levels(rows, lambda tk: None)
        assert len(out) == 1  # 0.05 <= 0.06 — рядом
        rows = [_r(price=300.0, size=200000), _r(price=300.20, size=100000)]
        out = ds.merge_levels(rows, lambda tk: None)
        assert len(out) == 2  # 0.20 > 0.06 — далеко

    def test_single_and_empty(self):
        assert ds.merge_levels([_r()]) == [_r()]
        assert ds.merge_levels([]) == []

    def test_sorted_output(self):
        rows = [_r(price=300.0, size=100000), _r(price=10.0, size=500000)]
        out = ds.merge_levels(rows, _tick_of)
        assert out[0]["size"] == 500000

    def test_ratio_top_replaces_size_sort(self):
        rows = [_r(price=300.0, size=200000, ratio=1.0),
                _r(price=300.01, size=100000, ratio=9.0)]
        out = ds.merge_levels(rows, _tick_of, sort_key=lambda r: -r["ratio"])
        assert len(out) == 1 and out[0]["ratio"] == 9.0

    def test_chain_into_one(self):
        # три соседних уровня: якорь тянет всех в пределe 2 тиков от себя
        rows = [_r(price=300.00, size=400000),
                _r(price=300.01, size=150000),
                _r(price=300.02, size=150000)]
        out = ds.merge_levels(rows, _tick_of)
        assert len(out) == 1 and out[0]["parts"] == 3
        assert out[0]["size"] == 700000
