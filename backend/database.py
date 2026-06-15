"""
SQLite async engine and session factory.
Database file: backend/inventory.db.
"""
import os
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "inventory.db")
DB_URL = f"sqlite+aiosqlite:///{DB_PATH}"

engine = create_async_engine(
    DB_URL,
    echo=False,
    # check_same_thread is unused for async access, but is kept for compatibility.
    connect_args={
        "check_same_thread": False,
        # Wait up to 30 seconds when SQLite is locked.
        "timeout": 30,
    },
    # Keep one sync connection so SQLite writes do not compete for the write lock.
    pool_size=1,
)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, connection_record):
    """Enable WAL mode and SQLite optimizations for each new connection."""
    cursor = dbapi_conn.cursor()
    # WAL lets writers avoid blocking readers and tolerates concurrent writes better.
    cursor.execute("PRAGMA journal_mode=WAL")
    # Extra safety net in milliseconds.
    cursor.execute("PRAGMA busy_timeout=30000")
    # NORMAL sync is enough for this app's performance and durability needs.
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


class Base(DeclarativeBase):
    pass


async def init_db():
    """Create all tables when missing. Called during startup."""
    from sqlalchemy import text
    from . import models  # noqa: F401 - import so tables are registered

    async def _inventory_needs_port_rebuild(conn) -> bool:
        columns = [
            row[1]
            for row in (await conn.execute(text("PRAGMA table_info(inventory_ip)"))).fetchall()
        ]
        if "port" not in columns:
            return True

        indexes = (await conn.execute(text("PRAGMA index_list(inventory_ip)"))).fetchall()
        for index in indexes:
            if not bool(index[2]):
                continue

            index_name = index[1]
            info = (await conn.execute(text(f"PRAGMA index_info({index_name})"))).fetchall()
            index_columns = [row[2] for row in info]
            if index_columns == ["hostname", "ip", "type"]:
                return True

        return False

    async def _rebuild_inventory_with_port(conn) -> None:
        await conn.execute(text("DROP TABLE IF EXISTS inventory_ip_new"))
        await conn.execute(text("""
            CREATE TABLE inventory_ip_new (
                id INTEGER PRIMARY KEY,
                device_id INTEGER,
                hostname VARCHAR(255) NOT NULL,
                ip VARCHAR(64) NOT NULL,
                port VARCHAR(16) NOT NULL DEFAULT '',
                type VARCHAR(32) NOT NULL,
                last_seen DATETIME NOT NULL,
                CONSTRAINT uq_hostname_ip_port_type UNIQUE (hostname, ip, port, type)
            )
        """))
        await conn.execute(text("""
            INSERT OR IGNORE INTO inventory_ip_new
                (id, device_id, hostname, ip, port, type, last_seen)
            SELECT
                id,
                device_id,
                hostname,
                ip,
                COALESCE(port, ''),
                type,
                last_seen
            FROM inventory_ip
        """))
        await conn.execute(text("DROP TABLE inventory_ip"))
        await conn.execute(text("ALTER TABLE inventory_ip_new RENAME TO inventory_ip"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_inventory_ip_id ON inventory_ip (id)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_inventory_ip_device_id ON inventory_ip (device_id)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_inventory_ip_hostname ON inventory_ip (hostname)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_inventory_ip_ip ON inventory_ip (ip)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_inventory_ip_port ON inventory_ip (port)"))

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        try:
            await conn.execute(text("ALTER TABLE devices ADD COLUMN hostname VARCHAR(255)"))
        except Exception:
            # Column already exists or table does not exist yet.
            pass
        try:
            await conn.execute(text("ALTER TABLE inventory_ip ADD COLUMN device_id INTEGER"))
        except Exception:
            # Column already exists or table does not exist yet.
            pass
        try:
            await conn.execute(text("ALTER TABLE inventory_ip ADD COLUMN port VARCHAR(16) NOT NULL DEFAULT ''"))
        except Exception:
            pass
        try:
            if await _inventory_needs_port_rebuild(conn):
                await _rebuild_inventory_with_port(conn)
        except Exception:
            pass
        try:
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_inventory_ip_device_id ON inventory_ip (device_id)"))
        except Exception:
            pass
        try:
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_inventory_ip_port ON inventory_ip (port)"))
        except Exception:
            pass


async def get_db():
    """FastAPI dependency that yields an async session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
