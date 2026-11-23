#!/usr/bin/env python3
"""Интерактивный execution-controller для tinvest-mcp.

Контракт MCP отдаёт post_order «доверенному UI/контроллеру», а не агенту.
Этот CLI и есть контроллер: он создаёт proposal с проверками риска, показывает
карточку и отправляет заявку ТОЛЬКО после нажатия Enter оператором.

Примеры:
    python confirm_trade.py --ticker VKCO --lots 1 --limit 214.60
    python confirm_trade.py --ticker VKCO --lots 1 --urgency patient --stop 213.80
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

SERVER = "/home/kvg/tinvest-mcp/.venv/bin/tinvest-mcp"
CONFIG = os.environ.get("TINVEST_MCP_CONFIG", "/home/kvg/.config/tinvest-mcp/config.toml")
ENV_FILE = os.environ.get("TINVEST_MCP_ENV_FILE", "/home/kvg/.config/tinvest-mcp/.env")
STATE_DIR = os.environ.get("TINVEST_MCP_STATE_DIR", "/home/kvg/.local/state/tinvest-mcp")


class Mcp:
    def __init__(self) -> None:
        env = dict(os.environ)
        env.update({
            "TINVEST_MCP_CONFIG": CONFIG,
            "TINVEST_MCP_ENV_FILE": ENV_FILE,
            "TINVEST_MCP_STATE_DIR": STATE_DIR,
        })
        self.proc = subprocess.Popen(
            [SERVER], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1, env=env,
        )
        self.q: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._reader, daemon=True).start()
        self._id = 0
        self.send({"jsonrpc": "2.0", "id": self._next(), "method": "initialize",
                   "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                              "clientInfo": {"name": "confirm-cli", "version": "1.0"}}})
        if not self._recv(60):
            raise SystemExit("MCP не ответил на initialize")
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        time.sleep(0.3)

    def _next(self) -> int:
        self._id += 1
        return self._id

    def _reader(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.strip()
            if line:
                self.q.put(line)

    def send(self, obj: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def _recv(self, timeout: float):
        end = time.time() + timeout
        while time.time() < end:
            try:
                return json.loads(self.q.get(timeout=max(0.2, end - time.time())))
            except queue.Empty:
                return None
        return None

    def call(self, name: str, args: dict, timeout: float = 90, retries: int = 3):
        for attempt in range(retries):
            self.send({"jsonrpc": "2.0", "id": self._next(), "method": "tools/call",
                       "params": {"name": name, "arguments": args}})
            resp = self._recv(timeout)
            if resp is None:
                raise SystemExit(f"{name}: нет ответа от MCP")
            if "error" in resp:
                raise SystemExit(f"{name}: {resp['error']}")
            text = resp["result"]["content"][0]["text"]
            transient = "UNAVAILABLE" in text or "tcp handshaker" in text
            if transient and attempt + 1 < retries:
                print("  … сеть Т-Инвестиций икнула, повтор")
                time.sleep(3)
                continue
            if resp["result"].get("isError"):
                raise SystemExit(f"{name}: {text[:400]}")
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
        raise SystemExit(f"{name}: не удалось после {retries} попыток")

    def close(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


def money(v) -> str:
    try:
        return f"{float(v):,.2f} ₽".replace(",", " ")
    except Exception:
        return str(v)


def card(p: dict) -> str:
    o = p.get("order", {})
    imp = p.get("portfolio_impact", {})
    lines = [
        "",
        "  ┌─ ПРЕДЛОЖЕНИЕ ─────────────────────────────────────",
        f"  │ {p['instrument']['ticker']:<8} {p['instrument']['name'][:28]}",
        f"  │ {o.get('direction')}  {o.get('type')}  {o.get('quantity_units')} шт   лимит {o.get('limit_price')}",
        f"  │ итого {money(o.get('estimated_total'))}   комиссия {money(o.get('commission'))}",
        f"  │ доля позиции после {float(imp.get('position_weight_after', 0)) * 100:.2f}%"
        f"   кэш после {money(imp.get('cash_after_estimated'))}",
        f"  │ статус {p.get('status')}",
        "  └───────────────────────────────────────────────────",
    ]
    warn = (p.get("price_selection") or {}).get("warning")
    if warn:
        lines.append(f"  ⚠ {warn}")
    failed = [c for c in p.get("risk_checks", []) if not c.get("passed")]
    for c in failed:
        lines.append(f"  ✗ {c.get('code')}: {c.get('message')}")
    return "\n".join(lines)


def ask() -> None:
    """Ждём Enter; без терминала (пайп) — отменяем, чтобы не отправить случайно."""
    try:
        ask()
    except EOFError:
        raise SystemExit("нет интерактивного ввода — заявка не отправлена")


def seconds_left(expires_at: str) -> float:
    try:
        dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        return (dt - datetime.now(timezone.utc)).total_seconds()
    except Exception:
        return 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description="Подтверждение и отправка заявки (execution controller)")
    ap.add_argument("--ticker", help="тикер, например VKCO")
    ap.add_argument("--uid", help="instrument_uid (вместо --ticker)")
    ap.add_argument("--direction", default="BUY", choices=["BUY", "SELL"])
    ap.add_argument("--lots", type=int, default=1)
    ap.add_argument("--limit", help="лимитная цена; без неё используется urgency")
    ap.add_argument("--urgency", default="balanced", choices=["balanced", "patient", "fast"])
    ap.add_argument("--stop", help="уровень стопа для памятки (в MCP стопов нет)")
    ap.add_argument("--dry-run", action="store_true", help="только показать карточку")
    a = ap.parse_args()
    if not a.ticker and not a.uid:
        ap.error("нужен --ticker или --uid")

    mcp = Mcp()
    try:
        acct = {}
        for attempt in range(6):
            acc = mcp.call("get_accounts", {})
            if isinstance(acc, dict):
                acc = acc.get("result", acc)
            acct = (acc[0] if isinstance(acc, list) and acc else {}) or {}
            if acct.get("execution_available"):
                break
            print(f"  … проверка торгового токена не прошла (попытка {attempt + 1}/6), жду 3 с")
            time.sleep(3)
        print(f"Счёт {acct.get('id')}  исполнение: {acct.get('execution_available')}"
              f"  уровень: {acct.get('execution_access_level')}")
        if not acct.get("execution_available"):
            print(f"  ⚠ проверка торгового токена не прошла: {acct.get('execution_block_reason')}")
            print("  ⚠ токен проверен по REST (FULL_ACCESS); если post_order откажет — увидим причину")
            if not a.dry_run:
                print("  ENTER — продолжить, Ctrl+C — отмена: ", end="", flush=True)
                ask()

        port = mcp.call("get_portfolio_summary", {})
        print(f"Кэш: {money(port.get('cash'))}   портфель: {money(port.get('total_value'))}")

        uid = a.uid
        if not uid:
            hits = mcp.call("search_instruments", {"query": a.ticker})
            if isinstance(hits, dict):
                hits = hits.get("result", hits)
            if not hits:
                print(f"Инструмент {a.ticker} не найден")
                return 1
            uid = hits[0]["uid"]
            print(f"Инструмент: {hits[0]['ticker']} {hits[0]['name']} (uid {uid})")

        snap = mcp.call("get_market_snapshot", {"instrument_uid": uid})
        print(f"Рынок: last {snap.get('last_price')}  bid {snap.get('best_bid')}"
              f"  ask {snap.get('best_ask')}  спред {snap.get('buy_price_hints', {}).get('spread', {}).get('width_pct')}%")

        args = {"instrument_uid": uid, "direction": a.direction, "order_type": "LIMIT",
                "quantity_lots": a.lots, "rationale": "CLI confirm"}
        if a.limit:
            args["limit_price"] = str(a.limit)
        else:
            args["urgency"] = a.urgency

        prop = mcp.call("create_order_proposal", args)
        print(card(prop))
        if prop.get("status") != "READY_FOR_CONFIRMATION":
            print("Заявка не прошла проверки — отправлять нечего.")
            return 1
        if a.dry_run:
            print("dry-run: заявка НЕ отправлена.")
            return 0

        left = seconds_left(prop.get("expires_at", ""))
        print(f"\n  Осталось {left:.0f} с до истечения предложения.")
        print("  ENTER — отправить заявку брокеру, Ctrl+C — отмена: ", end="", flush=True)
        ask()

        if seconds_left(prop.get("expires_at", "")) <= 1:
            print("  Предложение истекло, пересоздаю…")
            prop = mcp.call("create_order_proposal", args)
            print(card(prop))
            if prop.get("status") != "READY_FOR_CONFIRMATION":
                print("  Повторно не прошло проверки — отмена.")
                return 1
            print("  ENTER — отправить: ", end="", flush=True)
            ask()

        res = mcp.call("post_order", {"proposal_id": prop["proposal_id"]})
        print(f"\n  Отправлено: статус {res.get('status')}  id {res.get('proposal_id')}")
        if res.get("message"):
            print(f"  Брокер: {res['message']}")

        for _ in range(20):
            time.sleep(3)
            st = mcp.call("get_order_state", {"proposal_id": prop["proposal_id"]})
            status = st.get("status") if isinstance(st, dict) else None
            if status in ("FILLED", "REJECTED", "CANCELLED", "EXPIRED"):
                break
        print(f"  Итог: {status}  исполнено {st.get('lots_executed')} лот(ов)"
              f"  цена {st.get('executed_price')}  комиссия {money(st.get('commission'))}")

        if status == "FILLED" and a.direction == "BUY" and a.stop:
            print(f"\n  ⚠ Поставь в приложении стоп-лосс {a.stop} на {st.get('lots_executed')} шт — MCP стопов не умеет.")
        return 0
    finally:
        mcp.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nОтменено. Заявка не отправлена.")
        sys.exit(130)
