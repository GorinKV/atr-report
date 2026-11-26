const TABS = {
  portfolio: document.getElementById("tab-portfolio"),
  market: document.getElementById("tab-market"),
};

function el(id) { return document.getElementById(id); }

function fmt(n, digits = 2) {
  const v = Number(n);
  if (!isFinite(v)) return "—";
  const sign = v > 0 ? "+" : v < 0 ? "-" : "";
  return `${sign}${Math.abs(v).toLocaleString("ru-RU", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

function badge(cat) {
  const map = {
    breakeven: ["Безубыток", "breakeven"],
    protected: ["Защищ. прибыль", "protected"],
    plus_plain: ["В плюсе", "plus_plain"],
    minus: ["В минусе", "minus"],
  };
  const hit = map[cat] || ["—", "plus_plain"];
  return `<span class="badge ${hit[1]}">${hit[0]}</span>`;
}

async function loadPositions() {
  const d = await (await fetch("/miniapp/positions")).json();
  const s = d.stats || {};
  el("portfolio-summary").innerHTML =
    `Позиций: ${s.total ?? 0} · безубыток: ${s.breakeven ?? 0} · защищённая прибыль: ${s.protected ?? 0} · ` +
    `плюс без безубытка: ${(s.plus_plain ?? 0) - (s.breakeven ?? 0)} · в минусе: ${s.minus ?? 0}<br>` +
    `Equity: ${fmt(d.equity, 0)} ₽ · Кэш: ${fmt(d.cash, 0)} ₽`;

  const rows = d.positions || [];
  const tbody = el("portfolio-table").querySelector("tbody");
  tbody.innerHTML = rows.map((p) => {
    const pnlCls = p.pnl > 0 ? "num-pos" : p.pnl < 0 ? "num-neg" : "num-flat";
    const slCls = (p.sl || 0) > (p.avg || 0) ? "num-pos" : "num-neg";
    return `<tr>
      <td>${p.symbol || ""}</td>
      <td>${p.qty}</td>
      <td>${fmt(p.avg)}</td>
      <td>${fmt(p.last)}</td>
      <td class="${pnlCls}">${fmt(p.pnl, 2)}</td>
      <td class="${slCls}">${fmt(p.sl)}</td>
      <td>${badge(p.category)}</td>
    </tr>`;
  }).join("") || `<tr><td colspan="7">Позиций нет</td></tr>`;
}

async function loadMarket() {
  const d = await (await fetch("/miniapp/market")).json();
  const candidates = d.candidates || [];
  const m = d.market || {};
  el("market-summary").innerHTML =
    `Скринер ${d.generated_at} · ширина рынка ${m.breadth}% · оборот ${(m.turnover_rub / 1e9).toFixed(1)} млрд ₽`;

  const tbody = el("market-table").querySelector("tbody");
  tbody.innerHTML = candidates.map((c, i) => {
    const p = c.plan || {};
    return `<tr>
      <td>${i + 1}</td>
      <td>${c.ticker}</td>
      <td>${p.setup || ""}</td>
      <td>${fmt(c.last)}</td>
      <td class="${c.chg >= 0 ? "num-pos" : "num-neg"}">${fmt(c.chg, 2)}%</td>
      <td>${c.rvol}</td>
      <td>${c.atr_pct}</td>
      <td>${fmt(p.entry)}</td>
      <td>${fmt(p.stop)}</td>
      <td>${fmt(p.target)}</td>
      <td>${fmt(p.rr, 1)}</td>
      <td>${p.lots}</td>
    </tr>`;
  }).join("");
}

async function refresh() {
  try { await loadPositions(); } catch (e) { el("portfolio-summary").innerHTML = "Ошибка загрузки портфеля"; }
  try { await loadMarket(); } catch (e) { el("market-summary").innerHTML = "Ошибка загрузки рынка"; }
}

document.querySelectorAll(".tabs button").forEach((b) => {
  b.addEventListener("click", () => {
    document.querySelectorAll(".tabs button").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    for (const k of Object.keys(TABS)) TABS[k].hidden = k !== b.dataset.tab;
  });
});

refresh();
setInterval(refresh, 60000);
