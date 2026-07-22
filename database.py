from pathlib import Path

import aiosqlite


DATABASE_PATH = Path("data/reading_manager.db")


CREATE_SAVED_MESSAGES_TABLE = """
CREATE TABLE IF NOT EXISTS saved_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    saved_by_user_id TEXT NOT NULL,

    message_id TEXT NOT NULL,
    guild_id TEXT,
    channel_id TEXT NOT NULL,

    author_id TEXT NOT NULL,
    author_name TEXT NOT NULL,

    content TEXT NOT NULL,
    jump_url TEXT NOT NULL,

    message_created_at TEXT NOT NULL,
    saved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    status TEXT NOT NULL DEFAULT 'UNREAD'
        CHECK (status IN ('UNREAD', 'READ_KEEP')),

    UNIQUE(saved_by_user_id, message_id)
);
"""


CREATE_IGNORED_USERS_TABLE = """
CREATE TABLE IF NOT EXISTS ignored_users (
    saved_by_user_id TEXT NOT NULL,
    ignored_user_id TEXT NOT NULL,

    PRIMARY KEY (saved_by_user_id, ignored_user_id)
);
"""


CREATE_PENDING_RANGES_TABLE = """
CREATE TABLE IF NOT EXISTS pending_ranges (
    saved_by_user_id TEXT PRIMARY KEY,
    guild_id TEXT,
    channel_id TEXT NOT NULL,
    start_message_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


CREATE_SAVED_BATCHES_TABLE = """
CREATE TABLE IF NOT EXISTS saved_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    saved_by_user_id TEXT NOT NULL,
    title TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


CREATE_SAVED_BATCH_MESSAGES_TABLE = """
CREATE TABLE IF NOT EXISTS saved_batch_messages (
    batch_id INTEGER NOT NULL,
    saved_message_id INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK (position >= 0),

    PRIMARY KEY (batch_id, saved_message_id),
    UNIQUE (batch_id, position),

    FOREIGN KEY (batch_id)
        REFERENCES saved_batches(id)
        ON DELETE CASCADE,
    FOREIGN KEY (saved_message_id)
        REFERENCES saved_messages(id)
        ON DELETE CASCADE
);
"""


CREATE_SAVED_BATCH_MESSAGE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_saved_batch_messages_saved_message_id
ON saved_batch_messages (saved_message_id);
"""


async def initialize_database() -> None:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(DATABASE_PATH) as database:
        await database.execute("PRAGMA foreign_keys = ON;")
        await database.execute(CREATE_SAVED_MESSAGES_TABLE)
        await database.execute(CREATE_IGNORED_USERS_TABLE)
        await database.execute(CREATE_PENDING_RANGES_TABLE)
        await database.execute(CREATE_SAVED_BATCHES_TABLE)
        await database.execute(CREATE_SAVED_BATCH_MESSAGES_TABLE)
        await database.execute(CREATE_SAVED_BATCH_MESSAGE_INDEX)
        await database.commit()


async def set_pending_range_start(
    *,
    saved_by_user_id: str,
    guild_id: str | None,
    channel_id: str,
    start_message_id: str,
) -> None:
    query = """
    INSERT INTO pending_ranges (
        saved_by_user_id,
        guild_id,
        channel_id,
        start_message_id
    )
    VALUES (?, ?, ?, ?)
    ON CONFLICT(saved_by_user_id) DO UPDATE SET
        guild_id = excluded.guild_id,
        channel_id = excluded.channel_id,
        start_message_id = excluded.start_message_id,
        created_at = CURRENT_TIMESTAMP;
    """

    async with aiosqlite.connect(DATABASE_PATH) as database:
        await database.execute(
            query,
            (
                saved_by_user_id,
                guild_id,
                channel_id,
                start_message_id,
            ),
        )
        await database.commit()


async def get_pending_range(
    *,
    saved_by_user_id: str,
) -> aiosqlite.Row | None:
    query = """
    SELECT
        saved_by_user_id,
        guild_id,
        channel_id,
        start_message_id,
        created_at
    FROM pending_ranges
    WHERE saved_by_user_id = ?;
    """

    async with aiosqlite.connect(DATABASE_PATH) as database:
        database.row_factory = aiosqlite.Row

        cursor = await database.execute(
            query,
            (saved_by_user_id,),
        )

        return await cursor.fetchone()


async def delete_pending_range(
    *,
    saved_by_user_id: str,
) -> bool:
    query = """
    DELETE FROM pending_ranges
    WHERE saved_by_user_id = ?;
    """

    async with aiosqlite.connect(DATABASE_PATH) as database:
        cursor = await database.execute(
            query,
            (saved_by_user_id,),
        )
        await database.commit()

        return cursor.rowcount == 1


async def create_saved_batch(
    *,
    saved_by_user_id: str,
    title: str | None = None,
) -> int:
    query = """
    INSERT INTO saved_batches (
        saved_by_user_id,
        title
    )
    VALUES (?, ?);
    """

    normalized_title = title.strip() if title else None

    if not normalized_title:
        normalized_title = None

    async with aiosqlite.connect(DATABASE_PATH) as database:
        cursor = await database.execute(
            query,
            (
                saved_by_user_id,
                normalized_title,
            ),
        )
        await database.commit()

        batch_id = cursor.lastrowid

        if batch_id is None:
            raise RuntimeError("Failed to create saved batch")

        return batch_id


async def associate_saved_messages_with_batch(
    *,
    batch_id: int,
    saved_by_user_id: str,
    message_positions: list[tuple[int, int]],
) -> int:
    query = """
    INSERT OR IGNORE INTO saved_batch_messages (
        batch_id,
        saved_message_id,
        position
    )
    SELECT ?, ?, ?
    WHERE EXISTS (
        SELECT 1
        FROM saved_batches
        WHERE id = ?
          AND saved_by_user_id = ?
    )
      AND EXISTS (
        SELECT 1
        FROM saved_messages
        WHERE id = ?
          AND saved_by_user_id = ?
    );
    """

    if any(position < 0 for _, position in message_positions):
        raise ValueError("Batch message positions cannot be negative")

    associated_count = 0

    async with aiosqlite.connect(DATABASE_PATH) as database:
        await database.execute("PRAGMA foreign_keys = ON;")

        for saved_message_id, position in message_positions:
            cursor = await database.execute(
                query,
                (
                    batch_id,
                    saved_message_id,
                    position,
                    batch_id,
                    saved_by_user_id,
                    saved_message_id,
                    saved_by_user_id,
                ),
            )
            associated_count += cursor.rowcount

        await database.commit()

    return associated_count


async def ignore_user(
    *,
    saved_by_user_id: str,
    ignored_user_id: str,
) -> bool:
    query = """
    INSERT OR IGNORE INTO ignored_users (
        saved_by_user_id,
        ignored_user_id
    )
    VALUES (?, ?);
    """

    async with aiosqlite.connect(DATABASE_PATH) as database:
        cursor = await database.execute(
            query,
            (
                saved_by_user_id,
                ignored_user_id,
            ),
        )
        await database.commit()

        return cursor.rowcount == 1


async def unignore_user(
    *,
    saved_by_user_id: str,
    ignored_user_id: str,
) -> bool:
    query = """
    DELETE FROM ignored_users
    WHERE saved_by_user_id = ?
      AND ignored_user_id = ?;
    """

    async with aiosqlite.connect(DATABASE_PATH) as database:
        cursor = await database.execute(
            query,
            (
                saved_by_user_id,
                ignored_user_id,
            ),
        )
        await database.commit()

        return cursor.rowcount == 1


async def unignore_all_users(
    *,
    saved_by_user_id: str,
) -> int:
    query = """
    DELETE FROM ignored_users
    WHERE saved_by_user_id = ?;
    """

    async with aiosqlite.connect(DATABASE_PATH) as database:
        cursor = await database.execute(
            query,
            (saved_by_user_id,),
        )
        await database.commit()

        return cursor.rowcount


async def is_user_ignored(
    *,
    saved_by_user_id: str,
    ignored_user_id: str,
) -> bool:
    query = """
    SELECT 1
    FROM ignored_users
    WHERE saved_by_user_id = ?
      AND ignored_user_id = ?;
    """

    async with aiosqlite.connect(DATABASE_PATH) as database:
        cursor = await database.execute(
            query,
            (
                saved_by_user_id,
                ignored_user_id,
            ),
        )
        row = await cursor.fetchone()

        return row is not None


async def save_unread_message(
    *,
    saved_by_user_id: str,
    message_id: str,
    guild_id: str | None,
    channel_id: str,
    author_id: str,
    author_name: str,
    content: str,
    jump_url: str,
    message_created_at: str,
) -> bool:
    query = """
    INSERT OR IGNORE INTO saved_messages (
        saved_by_user_id,
        message_id,
        guild_id,
        channel_id,
        author_id,
        author_name,
        content,
        jump_url,
        message_created_at,
        status
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'UNREAD');
    """

    values = (
        saved_by_user_id,
        message_id,
        guild_id,
        channel_id,
        author_id,
        author_name,
        content,
        jump_url,
        message_created_at,
    )

    async with aiosqlite.connect(DATABASE_PATH) as database:
        cursor = await database.execute(query, values)
        await database.commit()

        return cursor.rowcount == 1
    

async def get_saved_messages(
    *,
    saved_by_user_id: str,
    status: str = "UNREAD",
    limit: int = 10,
    offset: int = 0,
) -> list[aiosqlite.Row]:
    query = """
    SELECT
        id,
        author_name,
        content,
        jump_url,
        message_created_at,
        status
    FROM saved_messages
    WHERE saved_by_user_id = ?
    """

    values: list[str | int] = [saved_by_user_id]

    if status != "ALL":
        query += " AND status = ?"
        values.append(status)

    query += """
    ORDER BY saved_at DESC, id DESC
    LIMIT ? OFFSET ?
    """

    values.append(limit)
    values.append(offset)

    async with aiosqlite.connect(DATABASE_PATH) as database:
        database.row_factory = aiosqlite.Row

        cursor = await database.execute(query, values)
        rows = await cursor.fetchall()

        return rows


async def count_saved_messages(
    *,
    saved_by_user_id: str,
    status: str = "UNREAD",
) -> int:
    query = """
    SELECT COUNT(*)
    FROM saved_messages
    WHERE saved_by_user_id = ?
    """

    values = [saved_by_user_id]

    if status != "ALL":
        query += " AND status = ?"
        values.append(status)

    async with aiosqlite.connect(DATABASE_PATH) as database:
        cursor = await database.execute(query, values)
        row = await cursor.fetchone()

        return row[0]
    

VALID_STATUSES = {"UNREAD", "READ_KEEP"}


async def update_saved_message_status(
    *,
    record_id: int,
    saved_by_user_id: str,
    status: str,
) -> bool:
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {status}")

    query = """
    UPDATE saved_messages
    SET status = ?
    WHERE id = ?
      AND saved_by_user_id = ?;
    """

    async with aiosqlite.connect(DATABASE_PATH) as database:
        cursor = await database.execute(
            query,
            (
                status,
                record_id,
                saved_by_user_id,
            ),
        )
        await database.commit()

        return cursor.rowcount == 1


async def delete_saved_message(
    *,
    record_id: int,
    saved_by_user_id: str,
) -> bool:
    query = """
    DELETE FROM saved_messages
    WHERE id = ?
      AND saved_by_user_id = ?;
    """

    async with aiosqlite.connect(DATABASE_PATH) as database:
        await database.execute("PRAGMA foreign_keys = ON;")
        cursor = await database.execute(
            query,
            (
                record_id,
                saved_by_user_id,
            ),
        )
        await database.commit()

        return cursor.rowcount == 1
