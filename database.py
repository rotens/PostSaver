from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import aiosqlite


DATABASE_PATH = Path("data/reading_manager.db")


@asynccontextmanager
async def _connect_database(
    *,
    rows: bool = False,
) -> AsyncIterator[aiosqlite.Connection]:
    async with aiosqlite.connect(DATABASE_PATH) as database:
        await database.execute("PRAGMA foreign_keys = ON;")

        if rows:
            database.row_factory = aiosqlite.Row

        yield database


@asynccontextmanager
async def _rollback_on_error(
    database: aiosqlite.Connection,
) -> AsyncIterator[None]:
    try:
        yield
    except Exception:
        await database.rollback()
        raise


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
    guild_name: str | None = None
    channel_name: str | None = None
    attachments: tuple[AttachmentToSave, ...] = ()


@dataclass(frozen=True)
class SavedMessageFilters:
    status: str = "UNREAD"
    keyword: str | None = None
    created_from: str | None = None
    created_before: str | None = None
    author_id: str | None = None
    channel_id: str | None = None
    guild_id: str | None = None


class SavedItemSort(StrEnum):
    DEFAULT = "DEFAULT"
    DATE_DESC = "DATE_DESC"
    DATE_ASC = "DATE_ASC"
    LENGTH_DESC = "LENGTH_DESC"
    LENGTH_ASC = "LENGTH_ASC"


@dataclass(frozen=True)
class RangeSaveResult:
    batch_id: int
    saved_count: int
    already_saved_count: int


@dataclass(frozen=True)
class ManualBatchAddResult:
    batch_id: int
    saved_message_id: int
    message_was_saved: bool
    association_was_created: bool
    position: int


@dataclass(frozen=True)
class SavedBatchDeleteResult:
    batch_id: int
    associations_removed: int


class UnsharedMessageDeleteMode(StrEnum):
    KEEP_OLDER = "KEEP_OLDER"
    DELETE_ALL = "DELETE_ALL"


@dataclass(frozen=True)
class SavedBatchMessageDeleteState:
    saved_message_id: int
    is_shared: bool
    is_older_or_equal: bool
    attachment_count: int


@dataclass(frozen=True)
class SavedBatchMessageDeletePreview:
    batch_id: int
    title: str | None
    total_message_count: int
    shared_message_count: int
    older_unshared_message_count: int
    newer_unshared_message_count: int
    keep_older_attachment_delete_count: int
    delete_all_attachment_delete_count: int
    message_states: tuple[SavedBatchMessageDeleteState, ...]


@dataclass(frozen=True)
class SavedBatchMessageDeleteResult:
    batch_id: int
    associations_removed: int
    saved_messages_deleted: int
    attachments_deleted: int
    shared_messages_kept: int
    older_unshared_messages_kept: int


class PendingRangeChangedError(RuntimeError):
    pass


class SavedBatchNotFoundError(LookupError):
    pass


class SavedBatchContentsChangedError(RuntimeError):
    pass


CREATE_SAVED_MESSAGES_TABLE = """
CREATE TABLE IF NOT EXISTS saved_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    saved_by_user_id TEXT NOT NULL,

    message_id TEXT NOT NULL,
    guild_id TEXT,
    guild_name TEXT,
    channel_id TEXT NOT NULL,
    channel_name TEXT,

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


SAVED_MESSAGE_LOCATION_COLUMNS = {
    "guild_name": "TEXT",
    "channel_name": "TEXT",
}


async def _add_missing_saved_message_location_columns(
    database: aiosqlite.Connection,
) -> None:
    cursor = await database.execute("PRAGMA table_info(saved_messages);")
    existing_columns = {
        row[1]
        for row in await cursor.fetchall()
    }

    for column_name, column_type in SAVED_MESSAGE_LOCATION_COLUMNS.items():
        if column_name in existing_columns:
            continue

        await database.execute(
            f"ALTER TABLE saved_messages "
            f"ADD COLUMN {column_name} {column_type};"
        )


async def initialize_database() -> None:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

    async with _connect_database() as database:
        await database.execute(CREATE_SAVED_MESSAGES_TABLE)
        await _add_missing_saved_message_location_columns(database)
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

    async with _connect_database() as database:
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

    async with _connect_database(rows=True) as database:
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

    async with _connect_database() as database:
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

    async with _connect_database() as database:
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

    async with _connect_database() as database:
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


async def delete_empty_saved_batch(
    *,
    batch_id: int,
    saved_by_user_id: str,
) -> bool:
    query = """
    DELETE FROM saved_batches
    WHERE id = ?
      AND saved_by_user_id = ?
      AND NOT EXISTS (
          SELECT 1
          FROM saved_batch_messages
          WHERE batch_id = saved_batches.id
      );
    """

    async with _connect_database() as database:
        cursor = await database.execute(
            query,
            (
                batch_id,
                saved_by_user_id,
            ),
        )
        await database.commit()

        return cursor.rowcount == 1


async def delete_saved_batch(
    *,
    batch_id: int,
    saved_by_user_id: str,
) -> SavedBatchDeleteResult | None:
    count_associations_query = """
    SELECT COUNT(batch_message.saved_message_id)
    FROM saved_batches AS saved_batch
    LEFT JOIN saved_batch_messages AS batch_message
      ON batch_message.batch_id = saved_batch.id
    WHERE saved_batch.id = ?
      AND saved_batch.saved_by_user_id = ?
    GROUP BY saved_batch.id;
    """
    delete_batch_query = """
    DELETE FROM saved_batches
    WHERE id = ?
      AND saved_by_user_id = ?;
    """

    async with _connect_database() as database:
        await database.execute("BEGIN IMMEDIATE;")
        async with _rollback_on_error(database):
            cursor = await database.execute(
                count_associations_query,
                (
                    batch_id,
                    saved_by_user_id,
                ),
            )
            row = await cursor.fetchone()

            if row is None:
                await database.rollback()
                return None

            associations_removed = row[0]
            cursor = await database.execute(
                delete_batch_query,
                (
                    batch_id,
                    saved_by_user_id,
                ),
            )

            if cursor.rowcount != 1:
                raise RuntimeError("Failed to delete saved batch")

            await database.commit()

    return SavedBatchDeleteResult(
        batch_id=batch_id,
        associations_removed=associations_removed,
    )


async def _get_saved_batch_message_delete_preview(
    database: aiosqlite.Connection,
    *,
    batch_id: int,
    saved_by_user_id: str,
) -> SavedBatchMessageDeletePreview | None:
    batch_cursor = await database.execute(
        """
        SELECT title, created_at
        FROM saved_batches
        WHERE id = ?
          AND saved_by_user_id = ?;
        """,
        (batch_id, saved_by_user_id),
    )
    batch_row = await batch_cursor.fetchone()

    if batch_row is None:
        return None

    title, batch_created_at = batch_row
    message_cursor = await database.execute(
        """
        SELECT
            saved_message.id,
            EXISTS (
                SELECT 1
                FROM saved_batch_messages AS other_batch_message
                WHERE other_batch_message.saved_message_id = saved_message.id
                  AND other_batch_message.batch_id != ?
            ) AS is_shared,
            saved_message.saved_at <= ? AS is_older_or_equal,
            (
                SELECT COUNT(*)
                FROM saved_message_attachments AS attachment
                WHERE attachment.saved_message_id = saved_message.id
            ) AS attachment_count
        FROM saved_batch_messages AS batch_message
        JOIN saved_messages AS saved_message
          ON saved_message.id = batch_message.saved_message_id
        WHERE batch_message.batch_id = ?
          AND saved_message.saved_by_user_id = ?
        ORDER BY saved_message.id;
        """,
        (
            batch_id,
            batch_created_at,
            batch_id,
            saved_by_user_id,
        ),
    )
    rows = await message_cursor.fetchall()
    message_states = tuple(
        SavedBatchMessageDeleteState(
            saved_message_id=row[0],
            is_shared=bool(row[1]),
            is_older_or_equal=bool(row[2]),
            attachment_count=row[3],
        )
        for row in rows
    )
    shared_states = tuple(state for state in message_states if state.is_shared)
    older_unshared_states = tuple(
        state
        for state in message_states
        if not state.is_shared and state.is_older_or_equal
    )
    newer_unshared_states = tuple(
        state
        for state in message_states
        if not state.is_shared and not state.is_older_or_equal
    )

    return SavedBatchMessageDeletePreview(
        batch_id=batch_id,
        title=title,
        total_message_count=len(message_states),
        shared_message_count=len(shared_states),
        older_unshared_message_count=len(older_unshared_states),
        newer_unshared_message_count=len(newer_unshared_states),
        keep_older_attachment_delete_count=sum(
            state.attachment_count for state in newer_unshared_states
        ),
        delete_all_attachment_delete_count=sum(
            state.attachment_count
            for state in (*older_unshared_states, *newer_unshared_states)
        ),
        message_states=message_states,
    )


async def get_saved_batch_message_delete_preview(
    *,
    batch_id: int,
    saved_by_user_id: str,
) -> SavedBatchMessageDeletePreview | None:
    async with _connect_database() as database:
        return await _get_saved_batch_message_delete_preview(
            database,
            batch_id=batch_id,
            saved_by_user_id=saved_by_user_id,
        )


async def delete_saved_batch_with_unshared_messages(
    *,
    batch_id: int,
    saved_by_user_id: str,
    mode: UnsharedMessageDeleteMode,
    expected_message_states: tuple[SavedBatchMessageDeleteState, ...],
) -> SavedBatchMessageDeleteResult | None:
    try:
        validated_mode = UnsharedMessageDeleteMode(mode)
    except ValueError as error:
        raise ValueError(f"Unsupported unshared-message delete mode: {mode}") from error

    async with _connect_database() as database:
        await database.execute("BEGIN IMMEDIATE;")
        async with _rollback_on_error(database):
            preview = await _get_saved_batch_message_delete_preview(
                database,
                batch_id=batch_id,
                saved_by_user_id=saved_by_user_id,
            )

            if preview is None:
                await database.rollback()
                return None

            if preview.message_states != expected_message_states:
                raise SavedBatchContentsChangedError(
                    "The batch contents or deletion impact changed"
                )

            states_to_delete = tuple(
                state
                for state in preview.message_states
                if not state.is_shared
                and (
                    validated_mode is UnsharedMessageDeleteMode.DELETE_ALL
                    or not state.is_older_or_equal
                )
            )
            for state in states_to_delete:
                cursor = await database.execute(
                    """
                    DELETE FROM saved_messages
                    WHERE id = ?
                      AND saved_by_user_id = ?;
                    """,
                    (state.saved_message_id, saved_by_user_id),
                )
                if cursor.rowcount != 1:
                    raise SavedBatchContentsChangedError(
                        "A saved message changed during batch deletion"
                    )

            cursor = await database.execute(
                """
                DELETE FROM saved_batches
                WHERE id = ?
                  AND saved_by_user_id = ?;
                """,
                (batch_id, saved_by_user_id),
            )
            if cursor.rowcount != 1:
                raise SavedBatchContentsChangedError(
                    "The batch changed during deletion"
                )

            await database.commit()

    return SavedBatchMessageDeleteResult(
        batch_id=batch_id,
        associations_removed=preview.total_message_count,
        saved_messages_deleted=len(states_to_delete),
        attachments_deleted=sum(
            state.attachment_count for state in states_to_delete
        ),
        shared_messages_kept=preview.shared_message_count,
        older_unshared_messages_kept=(
            preview.older_unshared_message_count
            if validated_mode is UnsharedMessageDeleteMode.KEEP_OLDER
            else 0
        ),
    )


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

    async with _connect_database() as database:
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


async def get_recent_saved_batches(
    *,
    saved_by_user_id: str,
    limit: int = 25,
) -> list[aiosqlite.Row]:
    if not 1 <= limit <= 25:
        raise ValueError("Recent batch limit must be between 1 and 25")

    query = """
    SELECT
        saved_batch.id,
        saved_batch.title,
        saved_batch.created_at,
        COUNT(batch_message.saved_message_id) AS message_count
    FROM saved_batches AS saved_batch
    LEFT JOIN saved_batch_messages AS batch_message
      ON batch_message.batch_id = saved_batch.id
    WHERE saved_batch.saved_by_user_id = ?
    GROUP BY saved_batch.id
    ORDER BY saved_batch.created_at DESC, saved_batch.id DESC
    LIMIT ?;
    """

    async with _connect_database(rows=True) as database:
        cursor = await database.execute(
            query,
            (
                saved_by_user_id,
                limit,
            ),
        )

        return await cursor.fetchall()


async def _save_message_for_manual_batch(
    database: aiosqlite.Connection,
    *,
    saved_by_user_id: str,
    message: MessageToSave,
) -> tuple[int, bool]:
    save_message_query = """
    INSERT OR IGNORE INTO saved_messages (
        saved_by_user_id,
        message_id,
        guild_id,
        guild_name,
        channel_id,
        channel_name,
        author_id,
        author_name,
        content,
        jump_url,
        message_created_at,
        status
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'UNREAD');
    """
    get_saved_message_query = """
    SELECT id
    FROM saved_messages
    WHERE saved_by_user_id = ?
      AND message_id = ?;
    """

    cursor = await database.execute(
        save_message_query,
        (
            saved_by_user_id,
            message.message_id,
            message.guild_id,
            message.guild_name,
            message.channel_id,
            message.channel_name,
            message.author_id,
            message.author_name,
            message.content,
            message.jump_url,
            message.message_created_at,
        ),
    )
    message_was_saved = cursor.rowcount == 1

    cursor = await database.execute(
        get_saved_message_query,
        (
            saved_by_user_id,
            message.message_id,
        ),
    )
    row = await cursor.fetchone()

    if row is None:
        raise RuntimeError("Failed to find saved message record")

    saved_message_id = row[0]
    await _insert_saved_message_attachments(
        database,
        saved_message_id=saved_message_id,
        attachments=message.attachments,
    )

    return saved_message_id, message_was_saved


async def _append_saved_message_to_batch(
    database: aiosqlite.Connection,
    *,
    batch_id: int,
    saved_message_id: int,
) -> tuple[bool, int]:
    existing_association_query = """
    SELECT position
    FROM saved_batch_messages
    WHERE batch_id = ?
      AND saved_message_id = ?;
    """
    next_position_query = """
    SELECT COALESCE(MAX(position), -1) + 1
    FROM saved_batch_messages
    WHERE batch_id = ?;
    """
    associate_message_query = """
    INSERT INTO saved_batch_messages (
        batch_id,
        saved_message_id,
        position
    )
    VALUES (?, ?, ?);
    """

    cursor = await database.execute(
        existing_association_query,
        (
            batch_id,
            saved_message_id,
        ),
    )
    row = await cursor.fetchone()

    if row is not None:
        return False, row[0]

    cursor = await database.execute(next_position_query, (batch_id,))
    row = await cursor.fetchone()

    if row is None:
        raise RuntimeError("Failed to calculate batch message position")

    position = row[0]
    await database.execute(
        associate_message_query,
        (
            batch_id,
            saved_message_id,
            position,
        ),
    )

    return True, position


async def add_message_to_saved_batch(
    *,
    batch_id: int,
    saved_by_user_id: str,
    message: MessageToSave,
) -> ManualBatchAddResult:
    _validate_attachments(message.attachments)
    find_owned_batch_query = """
    SELECT 1
    FROM saved_batches
    WHERE id = ?
      AND saved_by_user_id = ?;
    """

    async with _connect_database() as database:
        await database.execute("BEGIN IMMEDIATE;")
        async with _rollback_on_error(database):
            cursor = await database.execute(
                find_owned_batch_query,
                (
                    batch_id,
                    saved_by_user_id,
                ),
            )

            if await cursor.fetchone() is None:
                raise SavedBatchNotFoundError(
                    "Saved batch does not exist for this user"
                )

            saved_message_id, message_was_saved = (
                await _save_message_for_manual_batch(
                    database,
                    saved_by_user_id=saved_by_user_id,
                    message=message,
                )
            )
            association_was_created, position = (
                await _append_saved_message_to_batch(
                    database,
                    batch_id=batch_id,
                    saved_message_id=saved_message_id,
                )
            )
            await database.commit()

    return ManualBatchAddResult(
        batch_id=batch_id,
        saved_message_id=saved_message_id,
        message_was_saved=message_was_saved,
        association_was_created=association_was_created,
        position=position,
    )


async def create_saved_batch_with_message(
    *,
    saved_by_user_id: str,
    title: str | None,
    message: MessageToSave,
) -> ManualBatchAddResult:
    _validate_attachments(message.attachments)
    normalized_title = title.strip() if title else None

    if not normalized_title:
        normalized_title = None

    create_batch_query = """
    INSERT INTO saved_batches (
        saved_by_user_id,
        title
    )
    VALUES (?, ?);
    """

    async with _connect_database() as database:
        await database.execute("BEGIN IMMEDIATE;")
        async with _rollback_on_error(database):
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

            saved_message_id, message_was_saved = (
                await _save_message_for_manual_batch(
                    database,
                    saved_by_user_id=saved_by_user_id,
                    message=message,
                )
            )
            association_was_created, position = (
                await _append_saved_message_to_batch(
                    database,
                    batch_id=batch_id,
                    saved_message_id=saved_message_id,
                )
            )
            await database.commit()

    return ManualBatchAddResult(
        batch_id=batch_id,
        saved_message_id=saved_message_id,
        message_was_saved=message_was_saved,
        association_was_created=association_was_created,
        position=position,
    )


async def count_saved_batches(
    *,
    saved_by_user_id: str,
    filters: SavedMessageFilters | None = None,
) -> int:
    if not saved_message_filters_are_active(filters):
        query = """
        SELECT COUNT(*)
        FROM saved_batches
        WHERE saved_by_user_id = ?;
        """
        values: list[str | int] = [saved_by_user_id]
    else:
        assert filters is not None
        where_clause, values = _build_batch_message_filter_clause(
            saved_by_user_id=saved_by_user_id,
            filters=filters,
        )
        query = f"""
        SELECT COUNT(DISTINCT saved_batch.id)
        FROM saved_batches AS saved_batch
        JOIN saved_batch_messages AS batch_message
          ON batch_message.batch_id = saved_batch.id
        JOIN saved_messages AS saved_message
          ON saved_message.id = batch_message.saved_message_id
         AND saved_message.saved_by_user_id = saved_batch.saved_by_user_id
        WHERE {where_clause};
        """

    async with _connect_database() as database:
        cursor = await database.execute(query, values)
        row = await cursor.fetchone()

        return row[0]


def saved_message_filters_are_active(
    filters: SavedMessageFilters | None,
) -> bool:
    if filters is None:
        return False

    return (
        filters.status != "ALL"
        or filters.keyword is not None
        or filters.created_from is not None
        or filters.created_before is not None
        or filters.author_id is not None
        or filters.channel_id is not None
        or filters.guild_id is not None
    )


def _build_batch_message_filter_clause(
    *,
    saved_by_user_id: str,
    filters: SavedMessageFilters,
    batch_alias: str = "saved_batch",
    message_alias: str = "saved_message",
) -> tuple[str, list[str | int]]:
    conditions = [f"{batch_alias}.saved_by_user_id = ?"]
    values: list[str | int] = [saved_by_user_id]

    if filters.status != "ALL":
        conditions.append(f"{message_alias}.status = ?")
        values.append(filters.status)

    if filters.keyword is not None:
        escaped_keyword = _escape_like_keyword(filters.keyword)
        pattern = f"%{escaped_keyword}%"
        conditions.append(
            "("
            f"LOWER({message_alias}.content) "
            "LIKE LOWER(?) ESCAPE '!' "
            "OR "
            f"LOWER(COALESCE({batch_alias}.title, '')) "
            "LIKE LOWER(?) ESCAPE '!'"
            ")"
        )
        values.extend((pattern, pattern))

    if filters.created_from is not None:
        conditions.append(f"{message_alias}.message_created_at >= ?")
        values.append(filters.created_from)

    if filters.created_before is not None:
        conditions.append(f"{message_alias}.message_created_at < ?")
        values.append(filters.created_before)

    if filters.author_id is not None:
        conditions.append(f"{message_alias}.author_id = ?")
        values.append(filters.author_id)

    if filters.channel_id is not None:
        conditions.append(f"{message_alias}.channel_id = ?")
        values.append(filters.channel_id)

    if filters.guild_id is not None:
        conditions.append(f"{message_alias}.guild_id = ?")
        values.append(filters.guild_id)

    return " AND ".join(conditions), values


def _saved_batch_order_by(
    sort: SavedItemSort,
    *,
    filters_active: bool,
) -> str:
    _validate_saved_item_sort(sort)
    length_column = (
        "matching_content_length"
        if filters_active
        else "total_content_length"
    )
    return {
        SavedItemSort.DEFAULT: (
            "saved_batch.created_at DESC, saved_batch.id DESC"
        ),
        SavedItemSort.DATE_DESC: (
            "saved_batch.created_at DESC, saved_batch.id DESC"
        ),
        SavedItemSort.DATE_ASC: (
            "saved_batch.created_at ASC, saved_batch.id ASC"
        ),
        SavedItemSort.LENGTH_DESC: (
            f"{length_column} DESC, saved_batch.id DESC"
        ),
        SavedItemSort.LENGTH_ASC: (
            f"{length_column} ASC, saved_batch.id ASC"
        ),
    }[sort]


async def get_saved_batches(
    *,
    saved_by_user_id: str,
    filters: SavedMessageFilters | None = None,
    sort: SavedItemSort = SavedItemSort.DEFAULT,
    limit: int = 5,
    offset: int = 0,
) -> list[aiosqlite.Row]:
    selected_filters = filters or SavedMessageFilters(status="ALL")
    filters_active = saved_message_filters_are_active(filters)
    matching_where_clause, matching_values = (
        _build_batch_message_filter_clause(
            saved_by_user_id=saved_by_user_id,
            filters=selected_filters,
            batch_alias="owned_batch",
        )
    )
    matching_batch_requirement = (
        "AND matching_stats.batch_id IS NOT NULL"
        if filters_active
        else ""
    )
    order_by = _saved_batch_order_by(
        sort,
        filters_active=filters_active,
    )
    query = f"""
    WITH total_stats AS (
        SELECT
            batch_messages.batch_id,
            COUNT(*) AS total_message_count,
            SUM(LENGTH(saved_message.content)) AS total_content_length
        FROM saved_batch_messages AS batch_messages
        JOIN saved_batches AS owned_batch
          ON owned_batch.id = batch_messages.batch_id
        JOIN saved_messages AS saved_message
          ON saved_message.id = batch_messages.saved_message_id
         AND saved_message.saved_by_user_id = owned_batch.saved_by_user_id
        WHERE owned_batch.saved_by_user_id = ?
        GROUP BY batch_messages.batch_id
    ),
    matching_stats AS (
        SELECT
            batch_messages.batch_id,
            COUNT(*) AS matching_message_count,
            SUM(LENGTH(saved_message.content)) AS matching_content_length,
            MIN(batch_messages.position) AS first_matching_position,
            MAX(batch_messages.position) AS last_matching_position
        FROM saved_batch_messages AS batch_messages
        JOIN saved_batches AS owned_batch
          ON owned_batch.id = batch_messages.batch_id
        JOIN saved_messages AS saved_message
          ON saved_message.id = batch_messages.saved_message_id
         AND saved_message.saved_by_user_id = owned_batch.saved_by_user_id
        WHERE {matching_where_clause}
        GROUP BY batch_messages.batch_id
    )
    SELECT
        saved_batch.id,
        saved_batch.title,
        saved_batch.created_at,
        COALESCE(total_stats.total_message_count, 0)
            AS total_message_count,
        COALESCE(matching_stats.matching_message_count, 0)
            AS matching_message_count,
        COALESCE(total_stats.total_content_length, 0)
            AS total_content_length,
        COALESCE(matching_stats.matching_content_length, 0)
            AS matching_content_length,
        COALESCE(total_stats.total_message_count, 0) AS message_count,
        first_message.id AS first_message_record_id,
        first_message.guild_id AS first_message_guild_id,
        first_message.guild_name AS first_message_guild_name,
        first_message.channel_id AS first_message_channel_id,
        first_message.channel_name AS first_message_channel_name,
        first_message.author_name AS first_message_author_name,
        first_message.content AS first_message_content,
        first_message.jump_url AS first_message_jump_url,
        first_message.message_created_at AS first_message_created_at,
        first_message.status AS first_message_status,
        last_message.id AS last_message_record_id,
        last_message.jump_url AS last_message_jump_url
    FROM saved_batches AS saved_batch
    LEFT JOIN total_stats
      ON total_stats.batch_id = saved_batch.id
    LEFT JOIN matching_stats
      ON matching_stats.batch_id = saved_batch.id
    LEFT JOIN saved_batch_messages AS first_batch_message
      ON first_batch_message.batch_id = saved_batch.id
     AND first_batch_message.position
         = matching_stats.first_matching_position
    LEFT JOIN saved_messages AS first_message
      ON first_message.id = first_batch_message.saved_message_id
     AND first_message.saved_by_user_id = saved_batch.saved_by_user_id
    LEFT JOIN saved_batch_messages AS last_batch_message
      ON last_batch_message.batch_id = saved_batch.id
     AND last_batch_message.position
         = matching_stats.last_matching_position
    LEFT JOIN saved_messages AS last_message
      ON last_message.id = last_batch_message.saved_message_id
     AND last_message.saved_by_user_id = saved_batch.saved_by_user_id
    WHERE saved_batch.saved_by_user_id = ?
      {matching_batch_requirement}
    ORDER BY {order_by}
    LIMIT ? OFFSET ?;
    """

    async with _connect_database(rows=True) as database:
        cursor = await database.execute(
            query,
            (
                saved_by_user_id,
                *matching_values,
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
    filters: SavedMessageFilters | None = None,
) -> int:
    selected_filters = filters or SavedMessageFilters(status="ALL")
    where_clause, values = _build_batch_message_filter_clause(
        saved_by_user_id=saved_by_user_id,
        filters=selected_filters,
    )
    query = f"""
    SELECT COUNT(*)
    FROM saved_batches AS saved_batch
    JOIN saved_batch_messages AS batch_message
      ON batch_message.batch_id = saved_batch.id
    JOIN saved_messages AS saved_message
      ON saved_message.id = batch_message.saved_message_id
     AND saved_message.saved_by_user_id = saved_batch.saved_by_user_id
    WHERE saved_batch.id = ?
      AND {where_clause};
    """

    async with _connect_database() as database:
        cursor = await database.execute(
            query,
            (
                batch_id,
                *values,
            ),
        )
        row = await cursor.fetchone()

        return row[0]


async def get_saved_messages_in_batch(
    *,
    batch_id: int,
    saved_by_user_id: str,
    filters: SavedMessageFilters | None = None,
    limit: int = 5,
    offset: int = 0,
) -> list[aiosqlite.Row]:
    selected_filters = filters or SavedMessageFilters(status="ALL")
    where_clause, values = _build_batch_message_filter_clause(
        saved_by_user_id=saved_by_user_id,
        filters=selected_filters,
    )
    query = f"""
    SELECT
        saved_message.id,
        saved_message.guild_id,
        saved_message.guild_name,
        saved_message.channel_id,
        saved_message.channel_name,
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
      AND {where_clause}
    ORDER BY batch_message.position ASC
    LIMIT ? OFFSET ?;
    """

    async with _connect_database(rows=True) as database:
        cursor = await database.execute(
            query,
            (
                batch_id,
                *values,
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
        guild_name,
        channel_id,
        channel_name,
        author_id,
        author_name,
        content,
        jump_url,
        message_created_at,
        status
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'UNREAD');
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

    async with _connect_database() as database:
        await database.execute("BEGIN;")
        async with _rollback_on_error(database):
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
                        message.guild_name,
                        message.channel_id,
                        message.channel_name,
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

    async with _connect_database() as database:
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

    async with _connect_database() as database:
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

    async with _connect_database() as database:
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

    async with _connect_database() as database:
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

    async with _connect_database() as database:
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
    guild_name: str | None = None,
    channel_name: str | None = None,
    attachments: Sequence[AttachmentToSave] = (),
) -> bool:
    _validate_attachments(attachments)

    query = """
    INSERT OR IGNORE INTO saved_messages (
        saved_by_user_id,
        message_id,
        guild_id,
        guild_name,
        channel_id,
        channel_name,
        author_id,
        author_name,
        content,
        jump_url,
        message_created_at,
        status
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'UNREAD');
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
        guild_name,
        channel_id,
        channel_name,
        author_id,
        author_name,
        content,
        jump_url,
        message_created_at,
    )

    async with _connect_database() as database:
        await database.execute("BEGIN;")
        async with _rollback_on_error(database):
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

    return was_inserted


def _escape_like_keyword(keyword: str) -> str:
    return (
        keyword
        .replace("!", "!!")
        .replace("%", "!%")
        .replace("_", "!_")
    )


def _build_saved_message_filter_clause(
    *,
    saved_by_user_id: str,
    filters: SavedMessageFilters,
    table_alias: str = "saved_message",
) -> tuple[str, list[str | int]]:
    conditions = [f"{table_alias}.saved_by_user_id = ?"]
    values: list[str | int] = [saved_by_user_id]

    if filters.status != "ALL":
        conditions.append(f"{table_alias}.status = ?")
        values.append(filters.status)

    if filters.keyword is not None:
        escaped_keyword = _escape_like_keyword(filters.keyword)
        conditions.append(
            f"LOWER({table_alias}.content) "
            "LIKE LOWER(?) ESCAPE '!'"
        )
        values.append(f"%{escaped_keyword}%")

    if filters.created_from is not None:
        conditions.append(f"{table_alias}.message_created_at >= ?")
        values.append(filters.created_from)

    if filters.created_before is not None:
        conditions.append(f"{table_alias}.message_created_at < ?")
        values.append(filters.created_before)

    if filters.author_id is not None:
        conditions.append(f"{table_alias}.author_id = ?")
        values.append(filters.author_id)

    if filters.channel_id is not None:
        conditions.append(f"{table_alias}.channel_id = ?")
        values.append(filters.channel_id)

    if filters.guild_id is not None:
        conditions.append(f"{table_alias}.guild_id = ?")
        values.append(filters.guild_id)

    return " AND ".join(conditions), values


def _validate_saved_item_sort(sort: SavedItemSort) -> None:
    if not isinstance(sort, SavedItemSort):
        raise ValueError(f"Invalid saved-item sort: {sort}")


def _saved_message_order_by(sort: SavedItemSort) -> str:
    _validate_saved_item_sort(sort)
    return {
        SavedItemSort.DEFAULT: (
            "saved_message.saved_at DESC, saved_message.id DESC"
        ),
        SavedItemSort.DATE_DESC: (
            "saved_message.message_created_at DESC, "
            "saved_message.id DESC"
        ),
        SavedItemSort.DATE_ASC: (
            "saved_message.message_created_at ASC, saved_message.id ASC"
        ),
        SavedItemSort.LENGTH_DESC: (
            "LENGTH(saved_message.content) DESC, saved_message.id DESC"
        ),
        SavedItemSort.LENGTH_ASC: (
            "LENGTH(saved_message.content) ASC, saved_message.id ASC"
        ),
    }[sort]


async def get_saved_messages(
    *,
    saved_by_user_id: str,
    filters: SavedMessageFilters | None = None,
    sort: SavedItemSort = SavedItemSort.DEFAULT,
    limit: int = 10,
    offset: int = 0,
) -> list[aiosqlite.Row]:
    selected_filters = filters or SavedMessageFilters()
    where_clause, values = _build_saved_message_filter_clause(
        saved_by_user_id=saved_by_user_id,
        filters=selected_filters,
    )
    order_by = _saved_message_order_by(sort)
    query = f"""
    SELECT
        saved_message.id,
        saved_message.guild_id,
        saved_message.guild_name,
        saved_message.channel_id,
        saved_message.channel_name,
        saved_message.author_name,
        saved_message.content,
        saved_message.jump_url,
        saved_message.message_created_at,
        saved_message.status
    FROM saved_messages AS saved_message
    WHERE {where_clause}
    ORDER BY {order_by}
    LIMIT ? OFFSET ?
    """

    values.append(limit)
    values.append(offset)

    async with _connect_database(rows=True) as database:
        cursor = await database.execute(query, values)
        rows = await cursor.fetchall()

        return rows


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

    async with _connect_database(rows=True) as database:
        cursor = await database.execute(query, values)
        rows = await cursor.fetchall()

    for row in rows:
        attachments_by_message[row["saved_message_id"]].append(row)

    return attachments_by_message


async def count_saved_messages(
    *,
    saved_by_user_id: str,
    filters: SavedMessageFilters | None = None,
) -> int:
    selected_filters = filters or SavedMessageFilters()
    where_clause, values = _build_saved_message_filter_clause(
        saved_by_user_id=saved_by_user_id,
        filters=selected_filters,
    )
    query = f"""
    SELECT COUNT(*)
    FROM saved_messages AS saved_message
    WHERE {where_clause};
    """

    async with _connect_database() as database:
        cursor = await database.execute(query, values)
        row = await cursor.fetchone()

        return row[0]


def _validate_autocomplete_limit(limit: int) -> None:
    if not 1 <= limit <= 25:
        raise ValueError("Autocomplete limit must be between 1 and 25")


def _autocomplete_like_pattern(current: str) -> str:
    return f"%{_escape_like_keyword(current.strip())}%"


async def get_saved_author_autocomplete_choices(
    *,
    saved_by_user_id: str,
    current: str,
    limit: int = 25,
) -> list[aiosqlite.Row]:
    _validate_autocomplete_limit(limit)
    query = """
    WITH latest_author_names AS (
        SELECT
            author_id,
            author_name,
            ROW_NUMBER() OVER (
                PARTITION BY author_id
                ORDER BY saved_at DESC, id DESC
            ) AS row_number
        FROM saved_messages
        WHERE saved_by_user_id = ?
    )
    SELECT author_id, author_name
    FROM latest_author_names
    WHERE row_number = 1
      AND (
          LOWER(author_name) LIKE LOWER(?) ESCAPE '!'
          OR author_id LIKE ? ESCAPE '!'
      )
    ORDER BY author_name COLLATE NOCASE, author_id
    LIMIT ?;
    """
    pattern = _autocomplete_like_pattern(current)

    async with _connect_database(rows=True) as database:
        cursor = await database.execute(
            query,
            (
                saved_by_user_id,
                pattern,
                pattern,
                limit,
            ),
        )

        return await cursor.fetchall()


async def get_saved_channel_autocomplete_choices(
    *,
    saved_by_user_id: str,
    current: str,
    guild_id: str | None = None,
    limit: int = 25,
) -> list[aiosqlite.Row]:
    _validate_autocomplete_limit(limit)
    conditions = ["saved_by_user_id = ?"]
    values: list[str | int] = [saved_by_user_id]

    if guild_id is not None:
        conditions.append("guild_id = ?")
        values.append(guild_id)

    query = f"""
    WITH latest_channel_names AS (
        SELECT
            channel_id,
            channel_name,
            guild_id,
            guild_name,
            ROW_NUMBER() OVER (
                PARTITION BY channel_id
                ORDER BY saved_at DESC, id DESC
            ) AS row_number
        FROM saved_messages
        WHERE {' AND '.join(conditions)}
    )
    SELECT
        channel_id,
        channel_name,
        guild_id,
        guild_name
    FROM latest_channel_names
    WHERE row_number = 1
      AND (
          LOWER(COALESCE(channel_name, ''))
              LIKE LOWER(?) ESCAPE '!'
          OR channel_id LIKE ? ESCAPE '!'
          OR LOWER(COALESCE(guild_name, ''))
              LIKE LOWER(?) ESCAPE '!'
      )
    ORDER BY
        COALESCE(guild_name, '') COLLATE NOCASE,
        COALESCE(channel_name, channel_id) COLLATE NOCASE,
        channel_id
    LIMIT ?;
    """
    pattern = _autocomplete_like_pattern(current)
    values.extend((pattern, pattern, pattern, limit))

    async with _connect_database(rows=True) as database:
        cursor = await database.execute(query, values)

        return await cursor.fetchall()


async def get_saved_guild_autocomplete_choices(
    *,
    saved_by_user_id: str,
    current: str,
    limit: int = 25,
) -> list[aiosqlite.Row]:
    _validate_autocomplete_limit(limit)
    query = """
    WITH latest_guild_names AS (
        SELECT
            guild_id,
            guild_name,
            ROW_NUMBER() OVER (
                PARTITION BY guild_id
                ORDER BY saved_at DESC, id DESC
            ) AS row_number
        FROM saved_messages
        WHERE saved_by_user_id = ?
          AND guild_id IS NOT NULL
    )
    SELECT guild_id, guild_name
    FROM latest_guild_names
    WHERE row_number = 1
      AND (
          LOWER(COALESCE(guild_name, ''))
              LIKE LOWER(?) ESCAPE '!'
          OR guild_id LIKE ? ESCAPE '!'
      )
    ORDER BY COALESCE(guild_name, guild_id) COLLATE NOCASE, guild_id
    LIMIT ?;
    """
    pattern = _autocomplete_like_pattern(current)

    async with _connect_database(rows=True) as database:
        cursor = await database.execute(
            query,
            (
                saved_by_user_id,
                pattern,
                pattern,
                limit,
            ),
        )

        return await cursor.fetchall()


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

    async with _connect_database() as database:
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

    async with _connect_database() as database:
        cursor = await database.execute(
            query,
            (
                record_id,
                saved_by_user_id,
            ),
        )
        await database.commit()

        return cursor.rowcount == 1
