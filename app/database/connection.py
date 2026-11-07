from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings


connect_args = {}

if settings.DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}


engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True
)


if settings.DATABASE_URL.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False
)


Base = declarative_base()


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        connection.execute(text("DROP VIEW IF EXISTS trace_browser"))
        connection.execute(text("""
            CREATE VIEW trace_browser AS
            SELECT
                datetime(created_at, 'localtime') AS time,
                CASE
                    WHEN event_type = 'state_transition' THEN 'state'
                    ELSE event_type
                END AS type,
                CASE
                    WHEN length(event_name) > 30
                    THEN substr(event_name, 1, 30) || '...'
                    ELSE event_name
                END AS event,
                CASE
                    WHEN length(coalesce(arguments, '')) > 40
                    THEN substr(arguments, 1, 40) || '...'
                    ELSE arguments
                END AS args,
                CASE
                    WHEN length(coalesce(result, '')) > 40
                    THEN substr(result, 1, 40) || '...'
                    ELSE result
                END AS result,
                round(latency_ms, 1) AS ms,
                input_tokens AS in_tok,
                output_tokens AS out_tok,
                round(estimated_cost, 6) AS cost,
                substr(coalesce(conversation_id, ''), 1, 8) AS conv
            FROM traces
            ORDER BY created_at DESC, id DESC
        """))