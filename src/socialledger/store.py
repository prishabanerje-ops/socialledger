"""Local SQLite snapshot store — the source of truth for trends.

Platforms limit how far back some metrics go, so every snapshot is kept here. All writes are
upserts, so running ``social snapshot`` twice on the same day is safe.

Upsert rule for metric rows: a new ``available`` value always wins; a failed or unavailable
fetch never overwrites a previously stored ``available`` value.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from socialledger.schema import (
    Account,
    Availability,
    DailyMetrics,
    Metric,
    MetricValue,
    Platform,
    Post,
    PostFormat,
    Source,
)

_MIGRATIONS: list[str] = [
    # 1: initial schema
    """
    CREATE TABLE account_snapshots (
        brand TEXT NOT NULL, platform TEXT NOT NULL, account_id TEXT NOT NULL,
        as_of TEXT NOT NULL, handle TEXT,
        followers REAL, availability TEXT NOT NULL, source TEXT NOT NULL, note TEXT,
        fetched_at TEXT NOT NULL,
        PRIMARY KEY (brand, platform, account_id, as_of)
    );
    CREATE TABLE metric_values (
        brand TEXT NOT NULL, platform TEXT NOT NULL, account_id TEXT NOT NULL,
        date TEXT NOT NULL, metric TEXT NOT NULL,
        value REAL, availability TEXT NOT NULL, source TEXT NOT NULL, note TEXT,
        fetched_at TEXT NOT NULL, api_version TEXT,
        PRIMARY KEY (brand, platform, account_id, date, metric)
    );
    CREATE INDEX idx_metric_values_range ON metric_values (brand, platform, date);
    CREATE TABLE posts (
        brand TEXT NOT NULL, platform TEXT NOT NULL, account_id TEXT NOT NULL,
        post_id TEXT NOT NULL, url TEXT, published_at TEXT NOT NULL,
        format TEXT NOT NULL, title TEXT, source TEXT NOT NULL,
        first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
        PRIMARY KEY (platform, account_id, post_id)
    );
    CREATE INDEX idx_posts_published ON posts (brand, platform, published_at);
    CREATE TABLE post_metric_values (
        platform TEXT NOT NULL, account_id TEXT NOT NULL, post_id TEXT NOT NULL,
        as_of TEXT NOT NULL, metric TEXT NOT NULL,
        value REAL, availability TEXT NOT NULL, source TEXT NOT NULL, note TEXT,
        fetched_at TEXT NOT NULL,
        PRIMARY KEY (platform, account_id, post_id, as_of, metric)
    );
    CREATE TABLE snapshot_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        brand TEXT NOT NULL, platform TEXT NOT NULL,
        started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL, detail TEXT
    );
    """,
]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._conn:
            yield self._conn

    def _migrate(self) -> None:
        with self._tx() as c:
            c.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY)")
            done = {r[0] for r in c.execute("SELECT version FROM schema_migrations")}
            for version, sql in enumerate(_MIGRATIONS, start=1):
                if version not in done:
                    c.executescript(sql)
                    c.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))

    @property
    def schema_version(self) -> int:
        row = self._conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        return int(row[0] or 0)

    # ---- writes -------------------------------------------------------------------------

    def upsert_account(self, account: Account) -> None:
        f = account.followers
        with self._tx() as c:
            c.execute(
                """
                INSERT INTO account_snapshots
                    (brand, platform, account_id, as_of, handle, followers, availability,
                     source, note, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (brand, platform, account_id, as_of) DO UPDATE SET
                    handle = COALESCE(excluded.handle, account_snapshots.handle),
                    followers = excluded.followers, availability = excluded.availability,
                    source = excluded.source, note = excluded.note,
                    fetched_at = excluded.fetched_at
                    WHERE excluded.availability = 'available'
                      OR account_snapshots.availability != 'available'
                """,
                (
                    account.brand,
                    account.platform.value,
                    account.account_id,
                    account.as_of.isoformat(),
                    account.handle,
                    f.value,
                    f.availability.value,
                    f.source.value,
                    f.note,
                    _now(),
                ),
            )

    def upsert_daily(self, rows: Iterable[DailyMetrics], api_version: str | None = None) -> int:
        params = [
            (
                d.brand,
                d.platform.value,
                d.account_id,
                d.date.isoformat(),
                m.value,
                mv.value,
                mv.availability.value,
                mv.source.value,
                mv.note,
                _now(),
                api_version,
            )
            for d in rows
            for m, mv in d.metrics.items()
        ]
        with self._tx() as c:
            c.executemany(
                """
                INSERT INTO metric_values
                    (brand, platform, account_id, date, metric, value, availability, source,
                     note, fetched_at, api_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (brand, platform, account_id, date, metric) DO UPDATE SET
                    value = excluded.value, availability = excluded.availability,
                    source = excluded.source, note = excluded.note,
                    fetched_at = excluded.fetched_at, api_version = excluded.api_version
                    WHERE excluded.availability = 'available'
                      OR metric_values.availability != 'available'
                """,
                params,
            )
        return len(params)

    def upsert_posts(self, posts: Iterable[Post]) -> int:
        now = _now()
        count = 0
        with self._tx() as c:
            for p in posts:
                source = next(iter(p.metrics.values())).source if p.metrics else Source.API
                c.execute(
                    """
                    INSERT INTO posts
                        (brand, platform, account_id, post_id, url, published_at, format, title,
                         source, first_seen, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (platform, account_id, post_id) DO UPDATE SET
                        url = COALESCE(excluded.url, posts.url),
                        title = COALESCE(excluded.title, posts.title),
                        format = CASE WHEN excluded.format = 'unknown' THEN posts.format
                                      ELSE excluded.format END,
                        last_seen = excluded.last_seen
                    """,
                    (
                        p.brand,
                        p.platform.value,
                        p.account_id,
                        p.post_id,
                        p.url,
                        p.published_at.isoformat(),
                        p.format.value,
                        p.title,
                        source.value,
                        now,
                        now,
                    ),
                )
                c.executemany(
                    """
                    INSERT INTO post_metric_values
                        (platform, account_id, post_id, as_of, metric, value, availability,
                         source, note, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (platform, account_id, post_id, as_of, metric) DO UPDATE SET
                        value = excluded.value, availability = excluded.availability,
                        source = excluded.source, note = excluded.note,
                        fetched_at = excluded.fetched_at
                        WHERE excluded.availability = 'available'
                      OR post_metric_values.availability != 'available'
                    """,
                    [
                        (
                            p.platform.value,
                            p.account_id,
                            p.post_id,
                            p.as_of.isoformat(),
                            m.value,
                            mv.value,
                            mv.availability.value,
                            mv.source.value,
                            mv.note,
                            now,
                        )
                        for m, mv in p.metrics.items()
                    ],
                )
                count += 1
        return count

    def log_snapshot(self, brand: str, platform: Platform, status: str, detail: str) -> None:
        now = _now()
        with self._tx() as c:
            c.execute(
                """INSERT INTO snapshot_log (brand, platform, started_at, finished_at, status,
                   detail) VALUES (?, ?, ?, ?, ?, ?)""",
                (brand, platform.value, now, now, status, detail),
            )

    def purge_older_than(self, platform: Platform, days: int, today: date | None = None) -> int:
        """Delete rows older than ``days`` for a platform (platform data-retention rules)."""
        cutoff = ((today or date.today()) - timedelta(days=days)).isoformat()
        with self._tx() as c:
            n = c.execute(
                "DELETE FROM metric_values WHERE platform = ? AND date < ?",
                (platform.value, cutoff),
            ).rowcount
            n += c.execute(
                "DELETE FROM post_metric_values WHERE platform = ? AND as_of < ?",
                (platform.value, cutoff),
            ).rowcount
            n += c.execute(
                "DELETE FROM account_snapshots WHERE platform = ? AND as_of < ?",
                (platform.value, cutoff),
            ).rowcount
            c.execute(
                """DELETE FROM posts WHERE platform = ? AND NOT EXISTS (
                     SELECT 1 FROM post_metric_values v WHERE v.platform = posts.platform
                     AND v.account_id = posts.account_id AND v.post_id = posts.post_id)""",
                (platform.value,),
            )
        return n

    # ---- reads --------------------------------------------------------------------------

    def daily(self, brand: str, platform: Platform, start: date, end: date) -> list[DailyMetrics]:
        rows = self._conn.execute(
            """SELECT * FROM metric_values WHERE brand = ? AND platform = ?
               AND date BETWEEN ? AND ? ORDER BY date, metric""",
            (brand, platform.value, start.isoformat(), end.isoformat()),
        ).fetchall()
        by_day: dict[tuple[str, str], DailyMetrics] = {}
        for r in rows:
            key = (r["account_id"], r["date"])
            if key not in by_day:
                by_day[key] = DailyMetrics(
                    brand=brand,
                    platform=platform,
                    account_id=r["account_id"],
                    date=date.fromisoformat(r["date"]),
                )
            by_day[key].metrics[Metric(r["metric"])] = _mv(r)
        return list(by_day.values())

    def account_history(
        self, brand: str, platform: Platform, start: date, end: date
    ) -> list[Account]:
        rows = self._conn.execute(
            """SELECT * FROM account_snapshots WHERE brand = ? AND platform = ?
               AND as_of BETWEEN ? AND ? ORDER BY as_of""",
            (brand, platform.value, start.isoformat(), end.isoformat()),
        ).fetchall()
        return [
            Account(
                brand=brand,
                platform=platform,
                account_id=r["account_id"],
                handle=r["handle"],
                as_of=date.fromisoformat(r["as_of"]),
                followers=_mv(r, value_col="followers"),
            )
            for r in rows
        ]

    def posts(
        self, brand: str, platform: Platform, start: date, end: date, as_of: date | None = None
    ) -> list[Post]:
        """Posts published in [start, end], with their latest observation up to ``as_of``."""
        end_ts = (end + timedelta(days=1)).isoformat()
        rows = self._conn.execute(
            """SELECT * FROM posts WHERE brand = ? AND platform = ?
               AND published_at >= ? AND published_at < ? ORDER BY published_at""",
            (brand, platform.value, start.isoformat(), end_ts),
        ).fetchall()
        limit = (as_of or date.max).isoformat()
        out: list[Post] = []
        for r in rows:
            latest = self._conn.execute(
                """SELECT MAX(as_of) FROM post_metric_values WHERE platform = ?
                   AND account_id = ? AND post_id = ? AND as_of <= ?""",
                (platform.value, r["account_id"], r["post_id"], limit),
            ).fetchone()[0]
            metrics: dict[Metric, MetricValue] = {}
            if latest:
                for m in self._conn.execute(
                    """SELECT * FROM post_metric_values WHERE platform = ? AND account_id = ?
                       AND post_id = ? AND as_of = ?""",
                    (platform.value, r["account_id"], r["post_id"], latest),
                ):
                    metrics[Metric(m["metric"])] = _mv(m)
            out.append(
                Post(
                    brand=brand,
                    platform=platform,
                    account_id=r["account_id"],
                    post_id=r["post_id"],
                    url=r["url"],
                    published_at=datetime.fromisoformat(r["published_at"]),
                    format=PostFormat(r["format"]),
                    title=r["title"],
                    as_of=date.fromisoformat(latest)
                    if latest
                    else date.fromisoformat(r["last_seen"][:10]),
                    metrics=metrics,
                )
            )
        return out

    def last_snapshot(self, brand: str, platform: Platform, status: str = "ok") -> str | None:
        row = self._conn.execute(
            """SELECT MAX(finished_at) FROM snapshot_log WHERE brand = ? AND platform = ?
               AND status = ?""",
            (brand, platform.value, status),
        ).fetchone()
        return row[0] if row and row[0] else None

    def platforms_with_data(self, brand: str) -> list[Platform]:
        rows = self._conn.execute(
            """SELECT DISTINCT platform FROM metric_values WHERE brand = ?
               UNION SELECT DISTINCT platform FROM posts WHERE brand = ?""",
            (brand, brand),
        ).fetchall()
        return sorted((Platform(r[0]) for r in rows), key=lambda p: p.value)


def _mv(row: sqlite3.Row, value_col: str = "value") -> MetricValue:
    return MetricValue(
        value=row[value_col],
        availability=Availability(row["availability"]),
        source=Source(row["source"]),
        note=row["note"],
    )
