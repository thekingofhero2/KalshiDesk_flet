# -*- coding: utf-8 -*-
"""本地 DuckDB 存储：所有 Kalshi 数据增量落库，界面统一从库中读取。

设计：
- 账户余额：每次同步插入一条快照（balance_snapshots），界面取最新一条并展示同步时间；
- 持仓 / 子账户余额：按主键 upsert，本次快照里已不存在的记录会被删除；
- 订单 / 成交 / 结算 / 转账：按业务主键 upsert，只新增或更新，不删除历史。
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from typing import Optional
from uuid import uuid4

import duckdb

from .store import DATA_DIR

DB_PATH = DATA_DIR / "kalshi.duckdb"

_lock = threading.RLock()
_conn: Optional[duckdb.DuckDBPyConnection] = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS balance_snapshots (
    env VARCHAR, key_id VARCHAR, ts TIMESTAMP,
    available DOUBLE, portfolio_value DOUBLE, payout DOUBLE, shards VARCHAR,
    PRIMARY KEY (env, key_id, ts)
);
CREATE TABLE IF NOT EXISTS positions (
    env VARCHAR, ticker VARCHAR, position DOUBLE, yes_qty DOUBLE, no_qty DOUBLE,
    exposure DOUBLE, realized_pnl DOUBLE, fees DOUBLE, total_traded DOUBLE,
    resting INTEGER, exchange_index INTEGER, updated_ts VARCHAR,
    PRIMARY KEY (env, ticker)
);
CREATE TABLE IF NOT EXISTS orders (
    env VARCHAR, order_id VARCHAR, client_order_id VARCHAR, ticker VARCHAR,
    side VARCHAR, side_txt VARCHAR, price DOUBLE, count DOUBLE, filled DOUBLE,
    remaining DOUBLE, status VARCHAR, tif VARCHAR, fees DOUBLE,
    subaccount DOUBLE, exchange_index INTEGER, created VARCHAR, updated_ts TIMESTAMP,
    PRIMARY KEY (env, order_id)
);
CREATE TABLE IF NOT EXISTS fills (
    env VARCHAR, fill_id VARCHAR, order_id VARCHAR, ticker VARCHAR, side VARCHAR,
    action VARCHAR, price DOUBLE, count DOUBLE, is_taker BOOLEAN, fees DOUBLE,
    created VARCHAR, subaccount DOUBLE, exchange_index INTEGER,
    PRIMARY KEY (env, fill_id)
);
CREATE TABLE IF NOT EXISTS subaccount_balances (
    env VARCHAR, exchange_index INTEGER, subaccount DOUBLE, balance DOUBLE,
    updated_ts VARCHAR, updated_at TIMESTAMP,
    PRIMARY KEY (env, exchange_index, subaccount)
);
CREATE TABLE IF NOT EXISTS settlements (
    env VARCHAR, ticker VARCHAR, exchange_index INTEGER, event_ticker VARCHAR,
    market_result VARCHAR, yes_count DOUBLE, yes_cost DOUBLE, no_count DOUBLE,
    no_cost DOUBLE, revenue DOUBLE, fee DOUBLE, settled_time VARCHAR,
    PRIMARY KEY (env, ticker, exchange_index)
);
CREATE TABLE IF NOT EXISTS transfers (
    env VARCHAR, transfer_id VARCHAR, amount DOUBLE, from_subaccount DOUBLE,
    to_subaccount DOUBLE, created_ts VARCHAR,
    PRIMARY KEY (env, transfer_id)
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id VARCHAR PRIMARY KEY, env VARCHAR, username VARCHAR, body VARCHAR,
    image VARCHAR, created TIMESTAMP
);
-- CF Benchmarks 实时/日线行情 tick（Kalshi 代理转发 cfbenchmarks.com）
CREATE TABLE IF NOT EXISTS cfb_ticks (
    env        VARCHAR,
    index_id   VARCHAR,              -- BTCUSD_RTI / SOLUSD_RTI / ETHUSD_RR ...
    value      DOUBLE,
    time_ms    BIGINT,               -- CF server time 毫秒时间戳（用于去重主键）
    server_time VARCHAR,             -- CF 原始 ISO 字符串（留着调试）
    ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (env, index_id, time_ms)
);
CREATE INDEX IF NOT EXISTS idx_cfb_ticks_hist ON cfb_ticks(env, index_id, ingested_at);
"""


def init() -> None:
    """应用启动时调用：建立全局共享连接，后续所有线程复用同一连接（内部加锁串行化）。"""
    conn()


def conn() -> duckdb.DuckDBPyConnection:
    global _conn
    with _lock:
        if _conn is None:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            _conn = duckdb.connect(str(DB_PATH))
            _conn.execute(_SCHEMA)
        return _conn


def _exec(sql: str, params=None) -> None:
    with _lock:
        conn().execute(sql, params or [])


def _query(sql: str, params=None) -> list:
    with _lock:
        cur = conn().execute(sql, params or [])
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


# ================= 写入 =================
def save_balance(env: str, key_id: str, b: dict) -> None:
    if not b:
        return
    ts = datetime.now()
    shards = json.dumps(b.get("shards") or [], ensure_ascii=False)
    _exec(
        "INSERT OR REPLACE INTO balance_snapshots "
        "(env, key_id, ts, available, portfolio_value, payout, shards) VALUES (?,?,?,?,?,?,?)",
        [env, key_id, ts, b.get("available"), b.get("portfolio_value"),
         b.get("payout"), shards],
    )


def save_positions(env: str, rows: list) -> None:
    with _lock:
        c = conn()
        tickers = []
        for p in rows or []:
            tickers.append(p["ticker"])
            c.execute(
                "INSERT OR REPLACE INTO positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [env, p.get("ticker"), p.get("position"), p.get("yes_qty"),
                 p.get("no_qty"), p.get("exposure"), p.get("realized_pnl"),
                 p.get("fees"), p.get("total_traded"), int(p.get("resting") or 0),
                 p.get("exchange_index"), p.get("updated_ts")],
            )
        if tickers:
            marks = ",".join("?" * len(tickers))
            c.execute(f"DELETE FROM positions WHERE env=? AND ticker NOT IN ({marks})",
                      [env, *tickers])
        else:
            c.execute("DELETE FROM positions WHERE env=?", [env])


def save_orders(env: str, rows: list) -> None:
    if not rows:
        return
    now = datetime.now()
    with _lock:
        conn().executemany(
            "INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [[env, o.get("order_id"), o.get("client_order_id"), o.get("ticker"),
              o.get("side"), o.get("side_txt"), o.get("price"), o.get("count"),
              o.get("filled"), o.get("remaining"), o.get("status"), o.get("tif"),
              o.get("fees"), o.get("subaccount"), o.get("exchange_index"),
              o.get("created"), now] for o in rows],
        )


def save_fills(env: str, rows: list) -> None:
    if not rows:
        return
    with _lock:
        conn().executemany(
            "INSERT OR REPLACE INTO fills VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [[env, f.get("fill_id"), f.get("order_id"), f.get("ticker"),
              f.get("side"), f.get("action"), f.get("price"), f.get("count"),
              f.get("is_taker"), f.get("fees"), f.get("created"),
              f.get("subaccount"), f.get("exchange_index")] for f in rows],
        )


def save_subaccounts(env: str, rows: list) -> None:
    now = datetime.now()
    with _lock:
        c = conn()
        c.execute("DELETE FROM subaccount_balances WHERE env=?", [env])
        if rows:
            c.executemany(
                "INSERT OR REPLACE INTO subaccount_balances VALUES (?,?,?,?,?,?)",
                [[env, s.get("exchange_index"), s.get("subaccount"),
                  s.get("balance"), s.get("updated_ts"), now] for s in rows],
            )


def save_settlements(env: str, rows: list) -> None:
    if not rows:
        return
    with _lock:
        conn().executemany(
            "INSERT OR REPLACE INTO settlements VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [[env, s.get("ticker"), s.get("exchange_index"), s.get("event_ticker"),
              s.get("market_result"), s.get("yes_count"), s.get("yes_cost"),
              s.get("no_count"), s.get("no_cost"), s.get("revenue"),
              s.get("fee"), s.get("settled_time")] for s in rows],
        )


def save_transfers(env: str, rows: list) -> None:
    if not rows:
        return
    with _lock:
        conn().executemany(
            "INSERT OR REPLACE INTO transfers VALUES (?,?,?,?,?,?)",
            [[env, r.get("transfer_id"), r.get("amount"), r.get("from_subaccount"),
              r.get("to_subaccount"), r.get("created_ts")] for r in rows],
        )


# ================= CF Benchmarks =================
def save_cfb_ticks(env: str, rows: list) -> None:
    """一批 CF Benchmarks tick 落库（批量 INSERT OR IGNORE 避免重复 server_time）。"""
    if not rows:
        return
    with _lock:
        conn().executemany(
            "INSERT OR IGNORE INTO cfb_ticks "
            "(env, index_id, value, time_ms, server_time) VALUES (?,?,?,?,?)",
            [[env, r.get("index_id"), r.get("value"), r.get("time_ms"),
              r.get("server_time")] for r in rows],
        )


def cfb_latest(env: str, index_ids: Optional[list[str]] = None) -> dict:
    """每个 index_id 取最新一条 tick → {index_id: {value, ingested_at}}。

    不传 index_ids 时返回库里所有 index_id 的最新快照。
    """
    sql = (
        "WITH ranked AS (SELECT index_id, value, ingested_at, "
        "ROW_NUMBER() OVER (PARTITION BY index_id ORDER BY ingested_at DESC) AS rn "
        "FROM cfb_ticks WHERE env=?"
    )
    params: list = [env]
    if index_ids:
        marks = ",".join("?" * len(index_ids))
        sql += f" AND index_id IN ({marks})"
        params.extend(index_ids)
    sql += ") SELECT index_id, value, ingested_at FROM ranked WHERE rn=1"
    out = {}
    for r in _query(sql, params):
        out[r["index_id"]] = r
    return out


def cfb_history(env: str, index_id: str, hours: float = 24.0,
                limit: int = 500) -> list[dict]:
    """拉某个 CF index 最近 N 小时的 tick，按时间正序返回（画曲线用）。"""
    cutoff = datetime.fromtimestamp(
        datetime.now().timestamp() - hours * 3600)
    return _query(
        "SELECT index_id, value, time_ms, server_time, ingested_at "
        "FROM cfb_ticks WHERE env=? AND index_id=? AND ingested_at >= ? "
        "ORDER BY ingested_at ASC LIMIT ?",
        [env, index_id, cutoff, int(limit)])


def persist(env: str, key_id: str, *, balance=None, positions=None, orders=None,
            subs=None, fills=None, settlements=None, transfers=None) -> None:
    """一次同步的所有结果落库（在 io 线程中调用）。"""
    if balance is not None:
        save_balance(env, key_id, balance)
    if positions is not None:
        save_positions(env, positions)
    if orders is not None:
        save_orders(env, orders)
    if subs is not None:
        save_subaccounts(env, subs)
    if fills is not None:
        save_fills(env, fills)
    if settlements is not None:
        save_settlements(env, settlements)
    if transfers is not None:
        save_transfers(env, transfers)


# ================= 读取 =================
def latest_balance(env: str, key_id: Optional[str] = None) -> dict:
    sql = "SELECT * FROM balance_snapshots WHERE env=?"
    params = [env]
    if key_id:
        sql += " AND key_id=?"
        params.append(key_id)
    rows = _query(sql + " ORDER BY ts DESC LIMIT 1", params)
    if not rows:
        return {}
    r = rows[0]
    try:
        shards = json.loads(r.get("shards") or "[]")
    except ValueError:
        shards = []
    return {
        "available": r.get("available") or 0.0,
        "portfolio_value": r.get("portfolio_value"),
        "payout": r.get("payout"),
        "shards": shards,
        "synced_at": r.get("ts"),
    }


def positions(env: str) -> list:
    rows = _query(
        "SELECT * FROM positions WHERE env=? ORDER BY ABS(COALESCE(exposure,0)) DESC", [env])
    for r in rows:
        r["resting"] = int(r.get("resting") or 0)
    return rows


def orders(env: str, status: Optional[str] = None, ticker: Optional[str] = None,
           limit: int = 1000) -> list:
    sql = "SELECT * FROM orders WHERE env=?"
    params = [env]
    if status:
        sql += " AND status=?"
        params.append(status)
    if ticker:
        sql += " AND ticker=?"
        params.append(ticker)
    sql += " ORDER BY created DESC NULLS LAST LIMIT ?"
    params.append(int(limit))
    return _query(sql, params)


def fills(env: str, ticker: Optional[str] = None, days: Optional[int] = None,
          limit: int = 1000) -> list:
    sql = "SELECT * FROM fills WHERE env=?"
    params = [env]
    if ticker:
        sql += " AND ticker=?"
        params.append(ticker)
    if days:
        cutoff = datetime.fromtimestamp(
            datetime.now().timestamp() - days * 86400).isoformat(timespec="seconds")
        sql += " AND created >= ?"
        params.append(cutoff)
    sql += " ORDER BY created DESC NULLS LAST LIMIT ?"
    params.append(int(limit))
    return _query(sql, params)


def subaccounts(env: str) -> list:
    return _query(
        "SELECT * FROM subaccount_balances WHERE env=? "
        "ORDER BY exchange_index NULLS FIRST, subaccount NULLS FIRST", [env])


def settlements(env: str, ticker: Optional[str] = None) -> list:
    sql = "SELECT * FROM settlements WHERE env=?"
    params = [env]
    if ticker:
        sql += " AND ticker=?"
        params.append(ticker)
    return _query(sql + " ORDER BY settled_time DESC NULLS LAST", params)


def transfers(env: str, limit: int = 50) -> list:
    return _query(
        "SELECT * FROM transfers WHERE env=? ORDER BY created_ts DESC NULLS LAST LIMIT ?",
        [env, int(limit)])


def load_state(env: str, key_id: Optional[str] = None, status: Optional[str] = None,
               ticker: Optional[str] = None, fill_ticker: Optional[str] = None,
               fill_days: Optional[int] = None, with_fills: bool = True) -> dict:
    """界面渲染用的整包数据（全部来自本地库）。"""
    state = {
        "balance": latest_balance(env, key_id),
        "positions": positions(env),
        "orders": orders(env, status, ticker),
        "subs": subaccounts(env),
    }
    if with_fills:
        state["fills"] = fills(env, fill_ticker, fill_days)
    return state


# ================= 分析聚合 =================
def analytics(env: str, days: int = 30) -> dict:
    """供「分析」页与 AI 问答使用的本地库汇总。"""
    cutoff = datetime.fromtimestamp(datetime.now().timestamp() - days * 86400)
    fills_row = _query(
        "SELECT COUNT(*) AS n, COALESCE(SUM(count),0) AS contracts, "
        "COALESCE(SUM(count * price),0) AS volume, COALESCE(SUM(fees),0) AS fees "
        "FROM fills WHERE env=? AND TRY_CAST(created AS TIMESTAMP) >= ?",
        [env, cutoff])
    orders_row = _query(
        "SELECT status, COUNT(*) AS n FROM orders WHERE env=? GROUP BY status", [env])
    settle_rows = _query(
        "SELECT ticker, market_result, yes_count, yes_cost, no_count, no_cost, "
        "revenue, fee, settled_time FROM settlements WHERE env=? "
        "ORDER BY settled_time DESC NULLS LAST LIMIT 200", [env])
    payout = 0.0
    cost = 0.0
    for s in settle_rows:
        result = (s.get("market_result") or "").lower()
        payout += (s.get("yes_count") or 0) if result == "yes" else 0
        payout += (s.get("no_count") or 0) if result == "no" else 0
        cost += (s.get("yes_cost") or 0) + (s.get("no_cost") or 0) + (s.get("fee") or 0)
    top = _query(
        "SELECT ticker, COUNT(*) AS fills, COALESCE(SUM(count),0) AS contracts, "
        "COALESCE(SUM(count * price),0) AS volume FROM fills WHERE env=? "
        "GROUP BY ticker ORDER BY volume DESC LIMIT 10", [env])
    pos = positions(env)
    return {
        "days": days,
        "fills": fills_row[0] if fills_row else {},
        "orders": {r["status"]: r["n"] for r in orders_row},
        "settlements": {
            "count": len(settle_rows), "payout": payout, "cost": cost,
            "net": payout - cost, "recent": settle_rows[:10],
        },
        "top_markets": top,
        "balance": latest_balance(env),
        "position_count": len(pos),
        "exposure": sum((p.get("exposure") or 0) for p in pos),
        "realized_pnl": sum((p.get("realized_pnl") or 0) for p in pos),
    }


# ================= 聊天室 =================
def add_chat_message(env: str, username: str, body: str, image: str = "") -> dict:
    msg = {
        "id": uuid4().hex,
        "env": env,
        "username": username,
        "body": body or "",
        "image": image or "",
        "created": datetime.now(),
    }
    _exec("INSERT INTO chat_messages VALUES (?,?,?,?,?,?)",
          [msg["id"], env, username, msg["body"], msg["image"], msg["created"]])
    return msg


def chat_messages(env: str, limit: int = 100) -> list:
    rows = _query(
        "SELECT * FROM (SELECT * FROM chat_messages WHERE env=? "
        "ORDER BY created DESC LIMIT ?) ORDER BY created ASC", [env, int(limit)])
    return rows


def clear_chat(env: str) -> None:
    _exec("DELETE FROM chat_messages WHERE env=?", [env])
