"""Database layer: Supabase Postgres when DATABASE_URL is set, otherwise local SQLite.

App code writes SQLite-style SQL with ? placeholders; for Postgres it is converted here.
Rows support row["col"], row[0], dict(row) and Jinja attribute access.
"""
import os
import sqlite3
import threading as _threading
import time as _time

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
IS_PG = DATABASE_URL.startswith(("postgres://", "postgresql://"))

class Unavailable(Exception):
    """The online database cannot be reached (no internet, or Supabase is down)."""


if IS_PG:
    import logging
    logging.getLogger("psycopg.pool").setLevel(logging.CRITICAL)   # no "failed to resolve host" spam when offline
    import psycopg
    from psycopg_pool import ConnectionPool, PoolTimeout
    IntegrityError = (psycopg.IntegrityError,)
    CONNECTION_ERRORS = (psycopg.OperationalError, psycopg.InterfaceError, PoolTimeout, Unavailable)
else:
    IntegrityError = (sqlite3.IntegrityError,)
    CONNECTION_ERRORS = (Unavailable,)


class Row(dict):
    __slots__ = ("_vals",)

    def __init__(self, cols, vals):
        super().__init__(zip(cols, vals))
        self._vals = tuple(vals)

    def __getitem__(self, k):
        return self._vals[k] if isinstance(k, int) else dict.__getitem__(self, k)

    def __iter__(self):          # iterate values like a tuple / sqlite3.Row; dict(row) still uses keys()
        return iter(self._vals)

    def __len__(self):
        return len(self._vals)


def _pg_row_factory(cursor):
    cols = [d.name for d in (cursor.description or [])]
    return lambda values: Row(cols, values)


def _sqlite_row_factory(cursor, values):
    return Row([d[0] for d in cursor.description], values)


class Result:
    def __init__(self, cur, lastrowid=None):
        self._cur = cur
        self.rowcount = cur.rowcount
        self.lastrowid = lastrowid if lastrowid is not None else getattr(cur, "lastrowid", None)

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def __iter__(self):
        return iter(self._cur.fetchall())


_pool = None


def _close_pool():
    if _pool is not None:
        _pool.close(timeout=2)


def _get_pool():
    global _pool
    if _pool is None:
        import atexit
        atexit.register(_close_pool)
        extra = {"check": ConnectionPool.check_connection} if hasattr(ConnectionPool, "check_connection") else {}
        _pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=int(os.environ.get("DB_POOL_SIZE", "10")),
                               kwargs={"prepare_threshold": None, "row_factory": _pg_row_factory},
                               open=True, timeout=8, reconnect_timeout=0, **extra)   # reconnect forever, never give up
    return _pool


def _net_ok(timeout=2.5):
    """Fast check that the database server can be reached at all (DNS + TCP), capped at `timeout` seconds."""
    from urllib.parse import urlparse
    import socket
    u = urlparse(DATABASE_URL)
    res = []

    def probe():
        try:
            with socket.create_connection((u.hostname, u.port or 5432), timeout=timeout):
                res.append(True)
        except OSError:
            pass
    t = _threading.Thread(target=probe, daemon=True)
    t.start()
    t.join(timeout + 0.5)
    return bool(res)


class DB:
    def __init__(self, sqlite_path=None):
        if IS_PG:
            if (_pool is None or not _health["ok"]) and not _net_ok():
                raise Unavailable("no internet - database host unreachable")
            try:
                self.raw = _get_pool().getconn()          # dead connections are replaced automatically
            except Exception as e:
                raise Unavailable(str(e)) from e
        else:
            self.raw = sqlite3.connect(sqlite_path, timeout=30, check_same_thread=False)
            self.raw.row_factory = _sqlite_row_factory
            self.raw.execute("PRAGMA foreign_keys = ON")

    @staticmethod
    def _sql(sql):
        return sql.replace("%", "%%").replace("?", "%s") if IS_PG else sql

    def execute(self, sql, params=()):
        cur = self.raw.cursor()
        cur.execute(self._sql(sql), tuple(params))
        return Result(cur)

    def insert(self, sql, params=()):
        """INSERT and return the new row id."""
        cur = self.raw.cursor()
        if IS_PG:
            cur.execute(self._sql(sql) + " RETURNING id", tuple(params))
            return cur.fetchone()[0]
        cur.execute(sql, tuple(params))
        return cur.lastrowid

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        if IS_PG:
            try:
                self.raw.rollback()
            except Exception:
                pass
            try:
                _get_pool().putconn(self.raw)
            except Exception:
                pass
        else:
            self.raw.close()

    def run_script(self, script):
        """Run ;-separated DDL, ignoring statements that fail (e.g. column already exists)."""
        for stmt in [s.strip() for s in script.split(";") if s.strip()]:
            try:
                self.raw.execute(stmt)
                self.raw.commit()
            except Exception:
                self.raw.rollback()


# ---------------------------------------------------------------- connection health (for automatic offline switching)
import threading as _threading
import time as _time

_health = {"ok": True, "checked": 0.0}
_listeners = []


def check(timeout=3):
    """Try the online database once. Returns True when reachable."""
    if not IS_PG:
        return True
    ok = False
    if _net_ok():
        try:
            with _get_pool().connection(timeout=timeout) as c:
                c.execute("SELECT 1")
            ok = True
        except Exception:
            ok = False
    was = _health["ok"]
    _health.update(ok=ok, checked=_time.time())
    if ok != was:
        print("Online database is", "BACK - switching to online" if ok else "UNREACHABLE - switching to offline study")
        for fn in _listeners:
            try:
                fn(ok)
            except Exception as e:
                print("health listener failed:", e)
    return ok


def is_up():
    return _health["ok"] if IS_PG else True


def on_change(fn):
    _listeners.append(fn)


def start_monitor(interval=5):
    if not IS_PG:
        return

    def loop():
        while True:
            check()
            _time.sleep(interval)
    _threading.Thread(target=loop, daemon=True, name="db-health").start()
