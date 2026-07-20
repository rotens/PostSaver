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


async def initialize_database() -> None:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(DATABASE_PATH) as database:
        await database.execute(CREATE_SAVED_MESSAGES_TABLE)
        await database.execute(CREATE_IGNORED_USERS_TABLE)
        await database.commit()


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
        cursor = await database.execute(
            query,
            (
                record_id,
                saved_by_user_id,
            ),
        )
        await database.commit()

        return cursor.rowcount == 1
