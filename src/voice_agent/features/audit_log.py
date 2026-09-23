"""Feature 5: audit log of PIN attempts and student lookups.

An append-only SQLite file (data/audit.db). Each row stores who asked, what
they asked for, which student(s) were returned and which field *names* were
spoken, never field values, PINs, phone numbers, addresses and so on.

Rows are hash-chained: row_hash = sha256(prev_hash + row contents), so editing
or deleting a row breaks the chain and `verify` reports it.

Commands:
  python -m voice_agent.features.audit_log export --since 2026-09-01 --out audit.csv
  python -m voice_agent.features.audit_log verify
  python -m voice_agent.features.audit_log purge          # older than retention_days
  python -m voice_agent.features.audit_log tail -n 20

Retention: config.json -> audit.retention_days (7 by default). Old rows are
purged on the first write after startup, then at most once an hour.
"""
import argparse
import csv
import hashlib
import json
import logging
import sqlite3
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[3]
IST = timezone(timedelta(hours=5, minutes=30))

COLUMNS = ["id", "ts_utc", "call_id", "staff_id", "staff_name", "event", "query", "status", "match_count",
           "students", "fields_returned", "llm_provider", "latency_ms", "prev_hash", "row_hash"]
_HASHED = COLUMNS[1:13]  # everything except id and the hashes themselves

# Never written to the log, whatever a caller passes in.
FORBIDDEN_KEYS = {"pin", "phone", "mobile", "address", "aadhaar", "date_of_birth", "dob", "parent_contact", "email"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc TEXT NOT NULL,
    call_id TEXT,
    staff_id TEXT,
    staff_name TEXT,
    event TEXT NOT NULL,
    query TEXT,
    status TEXT,
    match_count INTEGER,
    students TEXT,
    fields_returned TEXT,
    llm_provider TEXT,
    latency_ms INTEGER,
    prev_hash TEXT NOT NULL,
    row_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_ts ON audit(ts_utc);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
BEGIN SELECT RAISE(ABORT, 'audit rows are append-only'); END;
"""

GENESIS = "0" * 64


def _row_hash(prev_hash: str, values: dict) -> str:
    payload = json.dumps([values.get(c) for c in _HASHED], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256((prev_hash + payload).encode("utf-8")).hexdigest()


def _clean_query(query: dict | None) -> str | None:
    if not query:
        return None
    safe = {k: v for k, v in query.items() if k.lower() not in FORBIDDEN_KEYS and v not in (None, "", False)}
    return json.dumps(safe, ensure_ascii=False, sort_keys=True) if safe else None


class AuditLog:
    def __init__(self, path: str | Path = "data/audit.db", fail_closed: bool = True,
                 retention_days: int | None = None, purge_every_seconds: int = 3600):
        self.path = Path(path)
        if not self.path.is_absolute() and str(path) != ":memory:":
            self.path = ROOT / self.path
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fail_closed = fail_closed
        self.retention_days = retention_days
        self.purge_every = purge_every_seconds
        self._last_purge: float | None = None
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path) if str(path) == ":memory:" else str(self.path),
                                   check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)

    # -- writing --
    def new_call_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def record(self, *, event: str, call_id: str | None = None, staff_id: str | None = None,
               staff_name: str | None = None, query: dict | None = None, status: str | None = None,
               match_count: int | None = None, students: list[str] | None = None,
               fields_returned: list[str] | None = None, llm_provider: str | None = None,
               latency_ms: int | None = None) -> int:
        self._maybe_purge()
        fields = [f for f in (fields_returned or []) if f.lower() not in FORBIDDEN_KEYS]
        values = {
            "ts_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "call_id": call_id, "staff_id": staff_id, "staff_name": staff_name, "event": event,
            "query": _clean_query(query), "status": status, "match_count": match_count,
            "students": "; ".join(students) if students else None,
            "fields_returned": ",".join(sorted(set(fields))) if fields else None,
            "llm_provider": llm_provider, "latency_ms": latency_ms,
        }
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute("SELECT row_hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
                prev = row[0] if row else GENESIS
                values["prev_hash"] = prev
                values["row_hash"] = _row_hash(prev, values)
                cols = COLUMNS[1:]
                cur = self._db.execute(f"INSERT INTO audit ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                                       [values[c] for c in cols])
                self._db.execute("COMMIT")
                return cur.lastrowid
            except Exception:
                self._db.execute("ROLLBACK")
                raise

    # -- reading --
    def rows(self, since: str | None = None, until: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM audit WHERE 1=1", []
        if since:
            sql += " AND ts_utc >= ?"
            args.append(since)
        if until:
            sql += " AND ts_utc < ?"
            args.append(until)
        cur = self._db.execute(sql + " ORDER BY id", args)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]

    def verify(self) -> tuple[bool, str]:
        """Check the whole hash chain. Returns (ok, message)."""
        rows = self.rows()
        if not rows:
            return True, "Audit log is empty."
        prev = rows[0]["prev_hash"]  # after a purge the first row points at a removed row
        for r in rows:
            if r["prev_hash"] != prev:
                return False, f"Chain broken before row {r['id']}: a row was deleted or reordered."
            if _row_hash(prev, r) != r["row_hash"]:
                return False, f"Row {r['id']} was modified."
            prev = r["row_hash"]
        return True, f"OK: {len(rows)} rows, chain intact."

    def export_csv(self, out: str | Path, since: str | None = None, until: str | None = None) -> int:
        rows = self.rows(since, until)
        with open(out, "w", newline="", encoding="utf-8-sig") as f:  # BOM so Excel shows Devanagari
            w = csv.writer(f)
            w.writerow(["ts_ist"] + COLUMNS)
            for r in rows:
                ts = datetime.fromisoformat(r["ts_utc"].replace("Z", "+00:00")).astimezone(IST)
                w.writerow([ts.strftime("%Y-%m-%d %H:%M:%S")] + [r[c] for c in COLUMNS])
        return len(rows)

    def _maybe_purge(self):
        if not self.retention_days or (
                self._last_purge is not None and time.monotonic() - self._last_purge < self.purge_every):
            return
        self._last_purge = time.monotonic()
        try:
            removed = self.purge(self.retention_days)
            if removed:
                log.info("Audit log: purged %d rows older than %d days", removed, self.retention_days)
        except Exception as e:  # noqa: BLE001
            log.warning("Audit purge failed: %s", e)

    def purge(self, retention_days: int) -> int:
        """Delete rows older than retention_days (the remaining chain still verifies)."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat(timespec="seconds")
        with self._lock:
            cur = self._db.execute("DELETE FROM audit WHERE ts_utc < ?", (cutoff.replace("+00:00", "Z"),))
        return cur.rowcount

    def close(self):
        self._db.close()


class CallAudit:
    """AuditLog bound to one call: fills in call_id, staff and LLM provider automatically."""

    def __init__(self, log_: AuditLog, session=None):
        self.log = log_
        self.call_id = log_.new_call_id()
        self.session = session
        self.llm_provider: str | None = None

    @property
    def fail_closed(self) -> bool:
        return self.log.fail_closed

    def record(self, **kw):
        kw.setdefault("call_id", self.call_id)
        if self.session is not None:
            kw.setdefault("staff_id", getattr(self.session, "staff_id", None))
            kw.setdefault("staff_name", getattr(self.session, "staff_name", None))
        if kw.get("staff_id") is None and self.session is not None:
            kw["staff_id"] = getattr(self.session, "staff_id", None)
            kw["staff_name"] = getattr(self.session, "staff_name", None)
        kw.setdefault("llm_provider", self.llm_provider)
        return self.log.record(**kw)


def build_audit(cfg: dict) -> AuditLog | None:
    acfg = cfg.get("audit", {})
    if not acfg.get("enabled", True):
        return None
    # Rows older than retention_days are purged on the first write and then hourly.
    return AuditLog(acfg.get("file", "data/audit.db"), acfg.get("fail_closed", True),
                    retention_days=acfg.get("retention_days", 7))


def _cli(argv=None):
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    acfg = cfg.get("audit", {})
    ap = argparse.ArgumentParser(prog="python -m voice_agent.features.audit_log", description="Vani audit log")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export", help="write rows to CSV")
    e.add_argument("--since", help="YYYY-MM-DD (UTC)")
    e.add_argument("--until", help="YYYY-MM-DD (UTC), exclusive")
    e.add_argument("--out", default="audit.csv")
    sub.add_parser("verify", help="check the hash chain")
    sub.add_parser("purge", help="delete rows older than retention_days")
    t = sub.add_parser("tail", help="show the latest rows")
    t.add_argument("-n", type=int, default=20)
    args = ap.parse_args(argv)

    audit = AuditLog(acfg.get("file", "data/audit.db"))
    if args.cmd == "export":
        n = audit.export_csv(args.out, args.since, args.until)
        print(f"Wrote {n} rows to {args.out}")
    elif args.cmd == "verify":
        ok, msg = audit.verify()
        print(msg)
        return 0 if ok else 2
    elif args.cmd == "purge":
        print(f"Deleted {audit.purge(acfg.get('retention_days', 7))} rows")
    else:
        for r in audit.rows()[-args.n:]:
            print(f"{r['ts_utc']}  {r['staff_id'] or '-':6} {r['event']:12} {r['status'] or '':18} "
                  f"{r['students'] or ''}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
