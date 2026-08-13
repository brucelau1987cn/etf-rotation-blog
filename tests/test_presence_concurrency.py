import sqlite3
import threading
from pathlib import Path


def test_presence_new_identity_limit_is_atomic_across_concurrent_connections(tmp_path: Path):
    db_path = tmp_path / "presence.db"
    setup = sqlite3.connect(db_path)
    setup.executescript(
        """
        CREATE TABLE presence_sessions (
          visitor_id TEXT PRIMARY KEY,
          last_seen INTEGER NOT NULL,
          ip_key TEXT NOT NULL DEFAULT '',
          created_at INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX idx_presence_sessions_ip_created
          ON presence_sessions (ip_key, created_at);
        """
    )
    setup.commit()
    setup.close()

    barrier = threading.Barrier(2)
    changes: list[int] = []
    errors: list[Exception] = []
    sql = """
        INSERT INTO presence_sessions (visitor_id, last_seen, ip_key, created_at)
        SELECT ?, ?, ?, ?
        WHERE (SELECT COUNT(*) FROM presence_sessions WHERE ip_key = ? AND created_at >= ?) < ?
    """

    def insert(visitor_id: str):
        conn = sqlite3.connect(db_path, timeout=5, isolation_level=None)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            barrier.wait()
            before = conn.total_changes
            conn.execute(sql, (visitor_id, 1000, "same-ip", 1000, "same-ip", 400, 1))
            changes.append(conn.total_changes - before)
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=insert, args=(f"visitor-{i}",)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    verify = sqlite3.connect(db_path)
    count = verify.execute("SELECT COUNT(*) FROM presence_sessions WHERE ip_key = 'same-ip'").fetchone()[0]
    verify.close()

    assert errors == []
    assert sorted(changes) == [0, 1]
    assert count == 1
