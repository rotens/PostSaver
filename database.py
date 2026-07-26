from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import aiosqlite


DATABASE_PATH = Path("data/reading_manager.db")


@dataclass(frozen=True)
class AttachmentToSave:
    attachment_id: str
    filename: str
    url: str
    proxy_url: str
    content_type: str | None
    size: int
    description: str | None
    width: int | None
    height: int | None
    position: int


@dataclass(frozen=True)
class MessageToSave:
    message_id: str
    guild_id: str | None
    channel_id: str
    author_id: str
    author_name: str
    content: str
    jump_url: str
    message_created_at: str
    position: int
    attachments: tuple[AttachmentToSave, ...] = ()


@dataclass(frozen=True)
class RangeSaveResult:
    batch_id: int
    saved_count: int
    already_saved_count: int


class PendingRangeChangedError(RuntimeError):
    pass


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


CREATE_SAVED_MESSAGE_ATTACHMENTS_TABLE = """
CREATE TABLE IF NOT EXISTS saved_message_attachments (
    saved_message_id INTEGER NOT NULL,
    attachment_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    url TEXT NOT NULL,
    proxy_url TEXT NOT NULL,
    content_type TEXT,
    size INTEGER NOT NULL CHECK (size >= 0),
    description TEXT,
    width INTEGER CHECK (width IS NULL OR width >= 0),
    height INTEGER CHECK (height IS NULL OR height >= 0),
    position INTEGER NOT NULL CHECK (position >= 0),

    PRIMARY KEY (saved_message_id, attachment_id),
    UNIQUE (saved_message_id, position),

    FOREIGN KEY (saved_message_id)
        REFERENCES saved_messages(id)
        ON DELETE CASCADE
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
        await database.execute(CREATE_SAVED_MESSAGE_ATTACHMENTS_TABLE)
        await database.execute(CREATE_IGNORED_USERS_TABLE)
        await database.execute(CREATE_PENDING_RANGES_TABLE)
        await database.execute(CREATE_SAVED_BATCHES_TABLE)
        await database.execute(CREATE_SAVED_BATCH_MESSAGES_TABLE)
        await database.execute(CREATE_SAVED_BATCH_MESSAGE_INDEX)
        await database.commit()


def _validate_attachments(
    attachments: Sequence[AttachmentToSave],
) -> None:
    attachment_ids = [
        attachment.attachment_id
        for attachment in attachments
    ]
    positions = [
        attachment.position
        for attachment in attachments
    ]

    if len(attachment_ids) != len(set(attachment_ids)):
        raise ValueError("Attachment IDs must be unique")

    if len(positions) != len(set(positions)):
        raise ValueError("Attachment positions must be unique")

    for attachment in attachments:
        if attachment.position < 0:
            raise ValueError("Attachment positions cannot be negative")

        if attachment.size < 0:
            raise ValueError("Attachment sizes cannot be negative")

        if attachment.width is not None and attachment.width < 0:
            raise ValueError("Attachment widths cannot be negative")

        if attachment.height is not None and attachment.height < 0:
            raise ValueError("Attachment heights cannot be negative")


async def _insert_saved_message_attachments(
    database: aiosqlite.Connection,
    *,
    saved_message_id: int,
    attachments: Sequence[AttachmentToSave],
) -> int:
    query = """
    INSERT INTO saved_message_attachments (
        saved_message_id,
        attachment_id,
        filename,
        url,
        proxy_url,
        content_type,
        size,
        description,
        width,
        height,
        position
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(saved_message_id, attachment_id) DO NOTHING;
    """
    inserted_count = 0

    for attachment in attachments:
        cursor = await database.execute(
            query,
            (
                saved_message_id,
                attachment.attachment_id,
                attachment.filename,
                attachment.url,
                attachment.proxy_url,
                attachment.content_type,
                attachment.size,
                attachment.description,
                attachment.width,
                attachment.height,
                attachment.position,
            ),
        )
        inserted_count += cursor.rowcount

    return inserted_count


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


async def delete_pending_range_if_matches(
    *,
    saved_by_user_id: str,
    expected_start_message_id: str,
) -> bool:
    query = """
    DELETE FROM pending_ranges
    WHERE saved_by_user_id = ?
      AND start_message_id = ?;
    """

    async with aiosqlite.connect(DATABASE_PATH) as database:
        cursor = await database.execute(
            query,
            (
                saved_by_user_id,
                expected_start_message_id,
            ),
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


async def count_saved_batches(
    *,
    saved_by_user_id: str,
) -> int:
    query = """
    SELECT COUNT(*)
    FROM saved_batches
    WHERE saved_by_user_id = ?;
    """

    async with aiosqlite.connect(DATABASE_PATH) as database:
        cursor = await database.execute(
            query,
            (saved_by_user_id,),
        )
        row = await cursor.fetchone()

        return row[0]


async def get_saved_batches(
    *,
    saved_by_user_id: str,
    limit: int = 5,
    offset: int = 0,
) -> list[aiosqlite.Row]:
    query = """
    WITH batch_stats AS (
        SELECT
            batch_messages.batch_id,
            COUNT(*) AS message_count,
            MIN(batch_messages.position) AS first_position
        FROM saved_batch_messages AS batch_messages
        JOIN saved_batches AS owned_batch
          ON owned_batch.id = batch_messages.batch_id
        JOIN saved_messages AS saved_message
          ON saved_message.id = batch_messages.saved_message_id
         AND saved_message.saved_by_user_id = owned_batch.saved_by_user_id
        WHERE owned_batch.saved_by_user_id = ?
        GROUP BY batch_messages.batch_id
    )
    SELECT
        saved_batch.id,
        saved_batch.title,
        saved_batch.created_at,
        COALESCE(batch_stats.message_count, 0) AS message_count,
        first_message.id AS first_message_record_id,
        first_message.author_name AS first_message_author_name,
        first_message.content AS first_message_content,
        first_message.jump_url AS first_message_jump_url,
        first_message.message_created_at AS first_message_created_at,
        first_message.status AS first_message_status
    FROM saved_batches AS saved_batch
    LEFT JOIN batch_stats
      ON batch_stats.batch_id = saved_batch.id
    LEFT JOIN saved_batch_messages AS first_batch_message
      ON first_batch_message.batch_id = saved_batch.id
     AND first_batch_message.position = batch_stats.first_position
    LEFT JOIN saved_messages AS first_message
      ON first_message.id = first_batch_message.saved_message_id
     AND first_message.saved_by_user_id = saved_batch.saved_by_user_id
    WHERE saved_batch.saved_by_user_id = ?
    ORDER BY saved_batch.created_at DESC, saved_batch.id DESC
    LIMIT ? OFFSET ?;
    """

    async with aiosqlite.connect(DATABASE_PATH) as database:
        database.row_factory = aiosqlite.Row

        cursor = await database.execute(
            query,
            (
                saved_by_user_id,
                saved_by_user_id,
                limit,
                offset,
            ),
        )

        return await cursor.fetchall()


async def count_saved_messages_in_batch(
    *,
    batch_id: int,
    saved_by_user_id: str,
) -> int:
    query = """
    SELECT COUNT(*)
    FROM saved_batches AS saved_batch
    JOIN saved_batch_messages AS batch_message
      ON batch_message.batch_id = saved_batch.id
    JOIN saved_messages AS saved_message
      ON saved_message.id = batch_message.saved_message_id
     AND saved_message.saved_by_user_id = saved_batch.saved_by_user_id
    WHERE saved_batch.id = ?
      AND saved_batch.saved_by_user_id = ?;
    """

    async with aiosqlite.connect(DATABASE_PATH) as database:
        cursor = await database.execute(
            query,
            (
                batch_id,
                saved_by_user_id,
            ),
        )
        row = await cursor.fetchone()

        return row[0]


async def get_saved_messages_in_batch(
    *,
    batch_id: int,
    saved_by_user_id: str,
    limit: int = 5,
    offset: int = 0,
) -> list[aiosqlite.Row]:
    query = """
    SELECT
        saved_message.id,
        saved_message.author_name,
        saved_message.content,
        saved_message.jump_url,
        saved_message.message_created_at,
        saved_message.status,
        batch_message.position
    FROM saved_batches AS saved_batch
    JOIN saved_batch_messages AS batch_message
      ON batch_message.batch_id = saved_batch.id
    JOIN saved_messages AS saved_message
      ON saved_message.id = batch_message.saved_message_id
     AND saved_message.saved_by_user_id = saved_batch.saved_by_user_id
    WHERE saved_batch.id = ?
      AND saved_batch.saved_by_user_id = ?
    ORDER BY batch_message.position ASC
    LIMIT ? OFFSET ?;
    """

    async with aiosqlite.connect(DATABASE_PATH) as database:
        database.row_factory = aiosqlite.Row

        cursor = await database.execute(
            query,
            (
                batch_id,
                saved_by_user_id,
                limit,
                offset,
            ),
        )

        return await cursor.fetchall()


async def save_message_range_as_batch(
    *,
    saved_by_user_id: str,
    expected_start_message_id: str,
    title: str | None,
    messages: list[MessageToSave],
) -> RangeSaveResult:
    if not messages:
        raise ValueError("Cannot create an empty saved batch")

    positions = [message.position for message in messages]

    if any(position < 0 for position in positions):
        raise ValueError("Batch message positions cannot be negative")

    if len(positions) != len(set(positions)):
        raise ValueError("Batch message positions must be unique")

    for message in messages:
        _validate_attachments(message.attachments)

    normalized_title = title.strip() if title else None

    if not normalized_title:
        normalized_title = None

    check_pending_range_query = """
    SELECT 1
    FROM pending_ranges
    WHERE saved_by_user_id = ?
      AND start_message_id = ?;
    """

    create_batch_query = """
    INSERT INTO saved_batches (
        saved_by_user_id,
        title
    )
    VALUES (?, ?);
    """

    save_message_query = """
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

    get_saved_message_id_query = """
    SELECT id
    FROM saved_messages
    WHERE saved_by_user_id = ?
      AND message_id = ?;
    """

    associate_message_query = """
    INSERT INTO saved_batch_messages (
        batch_id,
        saved_message_id,
        position
    )
    VALUES (?, ?, ?);
    """

    delete_pending_range_query = """
    DELETE FROM pending_ranges
    WHERE saved_by_user_id = ?
      AND start_message_id = ?;
    """

    saved_count = 0

    async with aiosqlite.connect(DATABASE_PATH) as database:
        await database.execute("PRAGMA foreign_keys = ON;")

        try:
            await database.execute("BEGIN;")

            cursor = await database.execute(
                check_pending_range_query,
                (
                    saved_by_user_id,
                    expected_start_message_id,
                ),
            )

            if await cursor.fetchone() is None:
                raise PendingRangeChangedError(
                    "Pending range no longer matches the selected start"
                )

            cursor = await database.execute(
                create_batch_query,
                (
                    saved_by_user_id,
                    normalized_title,
                ),
            )
            batch_id = cursor.lastrowid

            if batch_id is None:
                raise RuntimeError("Failed to create saved batch")

            for message in messages:
                cursor = await database.execute(
                    save_message_query,
                    (
                        saved_by_user_id,
                        message.message_id,
                        message.guild_id,
                        message.channel_id,
                        message.author_id,
                        message.author_name,
                        message.content,
                        message.jump_url,
                        message.message_created_at,
                    ),
                )
                saved_count += cursor.rowcount

                cursor = await database.execute(
                    get_saved_message_id_query,
                    (
                        saved_by_user_id,
                        message.message_id,
                    ),
                )
                row = await cursor.fetchone()

                if row is None:
                    raise RuntimeError("Failed to find saved message record")

                await _insert_saved_message_attachments(
                    database,
                    saved_message_id=row[0],
                    attachments=message.attachments,
                )

                await database.execute(
                    associate_message_query,
                    (
                        batch_id,
                        row[0],
                        message.position,
                    ),
                )

            cursor = await database.execute(
                delete_pending_range_query,
                (
                    saved_by_user_id,
                    expected_start_message_id,
                ),
            )

            if cursor.rowcount != 1:
                raise PendingRangeChangedError(
                    "Pending range changed before completion"
                )

            await database.commit()
        except Exception:
            await database.rollback()
            raise

    return RangeSaveResult(
        batch_id=batch_id,
        saved_count=saved_count,
        already_saved_count=len(messages) - saved_count,
    )


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


async def get_ignored_user_ids(
    *,
    saved_by_user_id: str,
) -> set[str]:
    query = """
    SELECT ignored_user_id
    FROM ignored_users
    WHERE saved_by_user_id = ?;
    """

    async with aiosqlite.connect(DATABASE_PATH) as database:
        cursor = await database.execute(
            query,
            (saved_by_user_id,),
        )
        rows = await cursor.fetchall()

        return {row[0] for row in rows}


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
    attachments: Sequence[AttachmentToSave] = (),
) -> bool:
    _validate_attachments(attachments)

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

    get_saved_message_id_query = """
    SELECT id
    FROM saved_messages
    WHERE saved_by_user_id = ?
      AND message_id = ?;
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
        await database.execute("PRAGMA foreign_keys = ON;")

        try:
            await database.execute("BEGIN;")
            cursor = await database.execute(query, values)
            was_inserted = cursor.rowcount == 1

            if attachments:
                cursor = await database.execute(
                    get_saved_message_id_query,
                    (
                        saved_by_user_id,
                        message_id,
                    ),
                )
                row = await cursor.fetchone()

                if row is None:
                    raise RuntimeError("Failed to find saved message record")

                await _insert_saved_message_attachments(
                    database,
                    saved_message_id=row[0],
                    attachments=attachments,
                )

            await database.commit()
        except Exception:
            await database.rollback()
            raise

    return was_inserted
    

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


async def save_saved_message_attachments(
    *,
    saved_message_id: int,
    saved_by_user_id: str,
    attachments: Sequence[AttachmentToSave],
) -> int:
    if not attachments:
        return 0

    _validate_attachments(attachments)

    owner_query = """
    SELECT 1
    FROM saved_messages
    WHERE id = ?
      AND saved_by_user_id = ?;
    """

    async with aiosqlite.connect(DATABASE_PATH) as database:
        await database.execute("PRAGMA foreign_keys = ON;")

        cursor = await database.execute(
            owner_query,
            (
                saved_message_id,
                saved_by_user_id,
            ),
        )

        if await cursor.fetchone() is None:
            return 0

        try:
            await database.execute("BEGIN;")
            inserted_count = await _insert_saved_message_attachments(
                database,
                saved_message_id=saved_message_id,
                attachments=attachments,
            )

            await database.commit()
        except Exception:
            await database.rollback()
            raise

    return inserted_count


async def get_attachments_for_saved_messages(
    *,
    saved_by_user_id: str,
    saved_message_ids: list[int],
) -> dict[int, list[aiosqlite.Row]]:
    unique_message_ids = list(dict.fromkeys(saved_message_ids))

    if not unique_message_ids:
        return {}

    placeholders = ", ".join("?" for _ in unique_message_ids)
    query = f"""
    SELECT
        attachment.saved_message_id,
        attachment.attachment_id,
        attachment.filename,
        attachment.url,
        attachment.proxy_url,
        attachment.content_type,
        attachment.size,
        attachment.description,
        attachment.width,
        attachment.height,
        attachment.position
    FROM saved_message_attachments AS attachment
    JOIN saved_messages AS saved_message
      ON saved_message.id = attachment.saved_message_id
    WHERE saved_message.saved_by_user_id = ?
      AND attachment.saved_message_id IN ({placeholders})
    ORDER BY attachment.saved_message_id, attachment.position;
    """
    values: list[str | int] = [
        saved_by_user_id,
        *unique_message_ids,
    ]
    attachments_by_message: dict[int, list[aiosqlite.Row]] = {
        saved_message_id: []
        for saved_message_id in unique_message_ids
    }

    async with aiosqlite.connect(DATABASE_PATH) as database:
        database.row_factory = aiosqlite.Row

        cursor = await database.execute(query, values)
        rows = await cursor.fetchall()

    for row in rows:
        attachments_by_message[row["saved_message_id"]].append(row)

    return attachments_by_message


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
