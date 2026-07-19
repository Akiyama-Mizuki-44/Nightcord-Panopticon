"""
青源（Qingyuan）架构的存储层——自建监控 agent 上报数据的落库。SQLite 文件跟 config.yaml 同目录，
不额外引入依赖（sqlite3 是标准库）。一台面板一条时间序列，(panel, ts) 联合主键，
重复上报同一秒直接覆盖而不是报错，方便 agent 端重试。
"""
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "metrics.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS metrics (
  panel TEXT NOT NULL,
  ts INTEGER NOT NULL,
  cpu REAL,
  mem REAL,
  disk REAL,
  net_in_kbps REAL,
  net_out_kbps REAL,
  PRIMARY KEY (panel, ts)
);
CREATE INDEX IF NOT EXISTS idx_metrics_panel_ts ON metrics(panel, ts);
"""


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _connect() as conn:
        conn.executescript(_SCHEMA)


def insert_sample(panel, ts, cpu, mem, disk, net_in_kbps, net_out_kbps):
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO metrics (panel, ts, cpu, mem, disk, net_in_kbps, net_out_kbps)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(panel, ts) DO UPDATE SET
              cpu=excluded.cpu, mem=excluded.mem, disk=excluded.disk,
              net_in_kbps=excluded.net_in_kbps, net_out_kbps=excluded.net_out_kbps
            """,
            (panel, ts, cpu, mem, disk, net_in_kbps, net_out_kbps),
        )


def query_history(panel, since_ts):
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT ts, cpu, mem, disk, net_in_kbps, net_out_kbps
            FROM metrics WHERE panel = ? AND ts >= ?
            ORDER BY ts ASC
            """,
            (panel, since_ts),
        ).fetchall()
        return [dict(r) for r in rows]


def prune_older_than(cutoff_ts):
    with _connect() as conn:
        cur = conn.execute("DELETE FROM metrics WHERE ts < ?", (cutoff_ts,))
        return cur.rowcount
