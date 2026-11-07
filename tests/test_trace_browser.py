from sqlalchemy import text

from app.database.connection import engine, init_db


def test_trace_browser_view_is_compact_and_newest_first():
    init_db()

    with engine.connect() as connection:
        columns = connection.execute(text(
            "PRAGMA table_info(trace_browser)"
        )).all()
        names = [row[1] for row in columns]
        rows = connection.execute(text("""
            SELECT time, length(args), length(result), conv
            FROM trace_browser
            LIMIT 100
        """)).all()

    assert rows
    assert names == [
        "time", "type", "event", "args", "result", "ms",
        "in_tok", "out_tok", "cost", "conv",
    ]
    assert all(
        length is None or length <= 43
        for row in rows
        for length in (row[1], row[2])
    )
    assert all(len(row.conv) <= 8 for row in rows)
    with engine.connect() as connection:
        latest = connection.execute(text(
            "SELECT datetime(created_at, 'localtime') "
            "FROM traces ORDER BY created_at DESC LIMIT 1"
        )).scalar_one()
        assert rows[0].time == latest

        assert connection.execute(text("""
            SELECT count(*)
            FROM trace_browser
            WHERE time LIKE '2026-11-07 16:53:%'
        """)).scalar_one() > 0
