from datetime import UTC, date, datetime
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
)
from socialledger.store import Store

D1 = date(2026, 10, 1)


def _daily(value: MetricValue, day: date = D1) -> DailyMetrics:
    return DailyMetrics(
        brand="b",
        platform=Platform.YOUTUBE,
        account_id="acc",
        date=day,
        metrics={Metric.VIEWS: value},
    )


def test_migrations_are_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "s.sqlite"
    Store(path).close()
    with Store(path) as store:
        assert store.schema_version == 1


def test_upsert_daily_is_idempotent(tmp_path: Path) -> None:
    with Store(tmp_path / "s.sqlite") as store:
        store.upsert_daily([_daily(MetricValue.of(10))])
        store.upsert_daily([_daily(MetricValue.of(12))])
        rows = store.daily("b", Platform.YOUTUBE, D1, D1)
        assert len(rows) == 1
        assert rows[0].metrics[Metric.VIEWS].value == 12


def test_failed_fetch_never_overwrites_good_data(tmp_path: Path) -> None:
    with Store(tmp_path / "s.sqlite") as store:
        store.upsert_daily([_daily(MetricValue.of(10))])
        store.upsert_daily([_daily(MetricValue.missing(Availability.ERROR, "boom"))])
        mv = store.daily("b", Platform.YOUTUBE, D1, D1)[0].metrics[Metric.VIEWS]
        assert mv.value == 10 and mv.is_available


def test_gap_is_stored_explicitly(tmp_path: Path) -> None:
    with Store(tmp_path / "s.sqlite") as store:
        store.upsert_daily([_daily(MetricValue.missing(Availability.UNAVAILABLE_FROM_API))])
        mv = store.daily("b", Platform.YOUTUBE, D1, D1)[0].metrics[Metric.VIEWS]
        assert mv.value is None and mv.availability is Availability.UNAVAILABLE_FROM_API


def test_posts_return_latest_observation(tmp_path: Path) -> None:
    def post(as_of: date, views: float, fmt: PostFormat = PostFormat.SHORT) -> Post:
        return Post(
            brand="b",
            platform=Platform.YOUTUBE,
            account_id="acc",
            post_id="p1",
            published_at=datetime(2026, 10, 1, 12, tzinfo=UTC),
            format=fmt,
            as_of=as_of,
            metrics={Metric.VIEWS: MetricValue.of(views)},
        )

    with Store(tmp_path / "s.sqlite") as store:
        store.upsert_posts([post(date(2026, 10, 2), 100)])
        store.upsert_posts([post(date(2026, 10, 5), 250, PostFormat.UNKNOWN)])
        [p] = store.posts("b", Platform.YOUTUBE, D1, date(2026, 10, 31))
        assert p.metrics[Metric.VIEWS].value == 250
        assert p.format is PostFormat.SHORT  # unknown never overwrites a known format
        [early] = store.posts(
            "b", Platform.YOUTUBE, D1, date(2026, 10, 31), as_of=date(2026, 10, 3)
        )
        assert early.metrics[Metric.VIEWS].value == 100


def test_retention_purge(tmp_path: Path) -> None:
    with Store(tmp_path / "s.sqlite") as store:
        store.upsert_daily(
            [
                _daily(MetricValue.of(1), date(2025, 1, 1)),
                _daily(MetricValue.of(2), date(2026, 10, 1)),
            ]
        )
        store.upsert_account(
            Account(
                brand="b",
                platform=Platform.YOUTUBE,
                account_id="acc",
                followers=MetricValue.of(5),
                as_of=date(2025, 1, 1),
            )
        )
        removed = store.purge_older_than(Platform.YOUTUBE, 365, today=date(2026, 10, 22))
        assert removed == 2
        assert len(store.daily("b", Platform.YOUTUBE, date(2024, 1, 1), date(2026, 12, 1))) == 1


def test_snapshot_log(tmp_path: Path) -> None:
    with Store(tmp_path / "s.sqlite") as store:
        assert store.last_snapshot("b", Platform.YOUTUBE) is None
        store.log_snapshot("b", Platform.YOUTUBE, "ok", "done")
        assert store.last_snapshot("b", Platform.YOUTUBE) is not None
