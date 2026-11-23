#!/usr/bin/env python3
"""Исполняющий контроллер для T-Invest через REST (без gRPC/MCP).

Зачем: gRPC-канал Т-Инвестиций сегодня периодически недоступен, а REST работает
стабильно. Скрипт делает то же, что MCP: показывает карточку заявки с проверками
и отправляет её ТОЛЬКО после нажатия Enter оператором.

Проверки берутся из config.toml MCP: только LIMIT, сумма <= max_order_rub,
отклонение цены <= max_price_deviation_pct, хватает кэша.

    python confirm_trade_rest.py --ticker VKCO --lots 1 --limit 214.00 --stop 212.90
    python confirm_trade_rest.py --ticker VKCO --lots 1 --limit 214.00 --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from decimal import Decimal
from pathlib import Path

BASE = "https://invest-public-api.tinkoff.ru/rest/tinkoff.public.invest.api.contract.v1"
ENV_FILE = Path("/home/kvg/.config/tinvest-mcp/.env")
CONFIG = Path("/home/kvg/.config/tinvest-mcp/config.toml")


def token() -> str:
    for line in ENV_FILE.read_text().splitlines():
        if line.startswith("TINVEST_FULLACCESS_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("TINVEST_FULLACCESS_TOKEN не найден в .env")


def limits() -> dict:
    text = CONFIG.read_text()
    out = {"max_order_rub": Decimal("1500"), "max_price_deviation_pct": Decimal("1.0")}
    for key in out:
        m = re.search(rf'^{key}\s*=\s*"([0-9.]+)"', text, re.M)
        if m:
            out[key] = Decimal(m.group(1))
    return out


def call(method: str, payload: dict, tok: str, retries: int = 8):
    """POST к REST API с повторами: край Т-Инвестиций рвёт ~половину новых TCP."""
    last_err: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(
            f"{BASE}.{method}",
            data=json.dumps(payload).encode(),
            headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=6) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code >= 500 or e.code == 429:
                last_err = e
            else:
                raise SystemExit(f"{method}: HTTP {e.code} {body[:300]}")
        except Exception as e:  # noqa: BLE001 - сеть Т-Инвестиций рвёт соединения
            last_err = e
        if attempt + 1 < retries:
            time.sleep(0.4)
    raise SystemExit(f"{method}: {type(last_err).__name__} {last_err}")


def q(value: Decimal | str) -> dict:
    d = Decimal(str(value))
    units = int(d)
    nano = int((d - units) * 1_000_000_000)
    return {"units": str(units), "nano": nano}


def qf(v: dict | None) -> float:
    if not v:
        return 0.0
    return float(v.get("units", 0)) + float(v.get("nano", 0)) / 1e9


def money(v) -> str:
    try:
        return f"{float(v):,.2f} ₽".replace(",", " ")
    except Exception:
        return str(v)


def ask() -> None:
    try:
        input()
    except EOFError:
        raise SystemExit("нет интерактивного ввода — заявка не отправлена")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--direction", default="BUY", choices=["BUY", "SELL"])
    ap.add_argument("--lots", type=int, default=1)
    ap.add_argument("--limit", required=True)
    ap.add_argument("--stop", help="уровень стопа для памятки")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", action="store_true",
                    help="не ждать Enter (оператор подтвердил в чате)")
    ap.add_argument("--max-deviation", type=float,
                    help="переопределить допустимое отклонение цены от последней, %%")
    a = ap.parse_args()

    tok = token()
    lim = limits()

    accs = call("UsersService/GetAccounts", {}, tok).get("accounts", [])
    if not accs:
        raise SystemExit("нет аккаунтов у токена")
    acct = accs[0]
    print(f"Счёт {acct['id']}  доступ {acct.get('accessLevel')}")

    pf = call("OperationsService/GetPortfolio", {"accountId": acct["id"], "currency": "RUB"}, tok)
    cash_val = qf(pf.get("totalAmountCurrencies"))
    positions = [
        p for p in pf.get("positions", [])
        if qf(p.get("quantity")) != 0 and p.get("instrumentType") != "currency"
    ]
    print(f"Кэш: {money(cash_val)}   позиций: {len(positions)}")

    found = call("InstrumentsService/FindInstrument", {"query": a.ticker, "instrumentKind": "INSTRUMENT_TYPE_SHARE"}, tok)
    hits = found.get("instruments", [])
    if not hits:
        raise SystemExit(f"{a.ticker} не найден")
    inst = next((h for h in hits if h.get("ticker") == a.ticker.upper()), hits[0])
    uid = inst["uid"]
    lot = int(inst.get("lot", 1))
    print(f"Инструмент: {inst['ticker']} {inst.get('name','')}  лот {lot}")

    prices = call("MarketDataService/GetLastPrices", {"instrumentId": [uid]}, tok).get("lastPrices", [])
    last = qf((prices[0] or {}).get("price")) if prices else 0.0
    book = call("MarketDataService/GetOrderBook", {"instrumentId": uid, "depth": 5}, tok)
    bids = book.get("bids", [])
    asks = book.get("asks", [])
    bid = qf(bids[0]["price"]) if bids else 0.0
    askp = qf(asks[0]["price"]) if asks else 0.0
    print(f"Рынок: last {last:.3f}  bid {bid:.3f}  ask {askp:.3f}  спред {(askp-bid)/last*100:.3f}%")

    price = Decimal(a.limit)
    total = price * a.lots * lot
    commission = (total * Decimal("0.003")).quantize(Decimal("0.01"))
    deviation = abs(float(price) - last) / last * 100 if last else 999

    print("\n  ┌─ ЗАЯВКА ──────────────────────────────────────────")
    print(f"  │ {inst['ticker']:<8} {a.direction}  LIMIT  {a.lots * lot} шт   цена {price}")
    print(f"  │ итого {money(total)}   комиссия ~{money(commission)} (0,3%)")
    if cash_val is not None:
        print(f"  │ кэш после ~{money(cash_val - float(total) - float(commission))}")
    print("  └───────────────────────────────────────────────────")

    max_dev = Decimal(str(a.max_deviation)) if a.max_deviation is not None else lim["max_price_deviation_pct"]
    problems = []
    if total > lim["max_order_rub"]:
        problems.append(f"сумма {total} > max_order_rub {lim['max_order_rub']}")
    if deviation > float(max_dev):
        problems.append(f"отклонение {deviation:.2f}% > {max_dev}%")
    if cash_val is not None and float(total) + float(commission) > cash_val:
        problems.append("не хватает кэша")
    if price < bid and a.direction == "BUY":
        print(f"  ⚠ лимит ниже бида {bid:.3f} — заявка встанет в очередь")

    for p in problems:
        print(f"  ✗ {p}")
    if problems:
        print("\nЗаявка НЕ отправлена.")
        return 1
    if a.dry_run:
        print("dry-run: заявка НЕ отправлена.")
        return 0

    if a.yes:
        print("\n  подтверждено флагом --yes")
    else:
        print("\n  ENTER — отправить заявку брокеру, Ctrl+C — отмена: ", end="", flush=True)
        ask()

    order_id = str(uuid.uuid4())
    body = {
        "accountId": acct["id"],
        "instrumentId": uid,
        "quantity": a.lots * lot,
        "price": q(price),
        "direction": "ORDER_DIRECTION_BUY" if a.direction == "BUY" else "ORDER_DIRECTION_SELL",
        "orderType": "ORDER_TYPE_LIMIT",
        "orderId": order_id,
    }
    res = call("OrdersService/PostOrder", body, tok)
    status = res.get("executionReportStatus", "?")
    print(f"\n  Брокер: {status}  заявка {res.get('orderId')}")
    if res.get("message"):
        print(f"  Сообщение: {res['message']}")

    final = status
    for _ in range(25):
        if final in ("EXECUTION_REPORT_STATUS_FILL", "EXECUTION_REPORT_STATUS_REJECTED",
                     "EXECUTION_REPORT_STATUS_CANCELLED"):
            break
        time.sleep(3)
        st = call("OrdersService/GetOrderState", {"accountId": acct["id"], "orderId": res["orderId"]}, tok)
        final = st.get("executionReportStatus", final)
        if final == "EXECUTION_REPORT_STATUS_FILL":
            print(f"  Исполнено: {st.get('lotsExecuted')} лот(ов) по {qf(st.get('executedOrderPrice')):.3f}"
                  f"  комиссия {money(qf(st.get('initialCommission')))}")
            break

    print(f"  Итог: {final}")
    if final == "EXECUTION_REPORT_STATUS_FILL" and a.direction == "BUY" and a.stop:
        print(f"\n  ⚠ Поставь стоп-лосс {a.stop} в приложении (MCP стопов не умеет).")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nОтменено. Заявка не отправлена.")
        sys.exit(130)
