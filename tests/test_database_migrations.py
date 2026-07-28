import tempfile
import unittest
from pathlib import Path

import aiosqlite

import database


LEGACY_SAVED_MESSAGES_TABLE = """
CREATE TABLE saved_messages (
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


class DatabaseMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_database_path = database.DATABASE_PATH
        database.DATABASE_PATH = (
            Path(self.temporary_directory.name) / "legacy_reading_manager.db"
        )

    async def asyncTearDown(self) -> None:
        database.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    async def test_location_columns_are_added_without_losing_old_data(
        self,
    ) -> None:
        async with aiosqlite.connect(database.DATABASE_PATH) as connection:
            await connection.execute(LEGACY_SAVED_MESSAGES_TABLE)
            await connection.execute(
                """
                INSERT INTO saved_messages (
                    saved_by_user_id,
                    message_id,
                    guild_id,
                    channel_id,
                    author_id,
                    author_name,
                    content,
                    jump_url,
                    message_created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    "user-1",
                    "message-1",
                    "guild-1",
                    "channel-1",
                    "author-1",
                    "Author One",
                    "Legacy content",
                    "https://example.com/message-1",
                    "2026-07-20T00:00:00+00:00",
                ),
            )
            await connection.commit()

        await database.initialize_database()
        await database.initialize_database()

        async with aiosqlite.connect(database.DATABASE_PATH) as connection:
            cursor = await connection.execute(
                "PRAGMA table_info(saved_messages);"
            )
            column_names = {row[1] for row in await cursor.fetchall()}
            cursor = await connection.execute(
                """
                SELECT
                    message_id,
                    guild_id,
                    guild_name,
                    channel_id,
                    channel_name,
                    content,
                    status
                FROM saved_messages;
                """
            )
            saved_row = await cursor.fetchone()

        self.assertIn("guild_name", column_names)
        self.assertIn("channel_name", column_names)
        self.assertEqual(
            saved_row,
            (
                "message-1",
                "guild-1",
                None,
                "channel-1",
                None,
                "Legacy content",
                "UNREAD",
            ),
        )


if __name__ == "__main__":
    unittest.main()
