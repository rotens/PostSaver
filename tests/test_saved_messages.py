import tempfile
import unittest
from pathlib import Path

import aiosqlite

import database


class SavedMessageDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_database_path = database.DATABASE_PATH
        database.DATABASE_PATH = (
            Path(self.temporary_directory.name) / "test_reading_manager.db"
        )

        await database.initialize_database()

    async def asyncTearDown(self) -> None:
        database.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    async def save_message(
        self,
        *,
        saved_by_user_id: str = "user-1",
        message_id: str,
        guild_id: str | None = "guild-1",
    ) -> tuple[bool, int]:
        was_inserted = await database.save_unread_message(
            saved_by_user_id=saved_by_user_id,
            message_id=message_id,
            guild_id=guild_id,
            channel_id="channel-1",
            author_id=f"author-{message_id}",
            author_name=f"Author {message_id}",
            content=f"Content for {message_id}",
            jump_url=f"https://example.com/{message_id}",
            message_created_at="2026-07-23T00:00:00+00:00",
        )

        async with aiosqlite.connect(database.DATABASE_PATH) as connection:
            cursor = await connection.execute(
                """
                SELECT id
                FROM saved_messages
                WHERE saved_by_user_id = ?
                  AND message_id = ?;
                """,
                (
                    saved_by_user_id,
                    message_id,
                ),
            )
            row = await cursor.fetchone()

        return was_inserted, row[0]

    async def test_save_stores_metadata_with_unread_status(self) -> None:
        was_inserted = await database.save_unread_message(
            saved_by_user_id="user-1",
            message_id="message-1",
            guild_id=None,
            channel_id="channel-1",
            author_id="author-1",
            author_name="Author One",
            content="Saved content",
            jump_url="https://example.com/message-1",
            message_created_at="2026-07-23T00:00:00+00:00",
        )

        async with aiosqlite.connect(database.DATABASE_PATH) as connection:
            cursor = await connection.execute(
                """
                SELECT
                    saved_by_user_id,
                    message_id,
                    guild_id,
                    channel_id,
                    author_id,
                    author_name,
                    content,
                    jump_url,
                    message_created_at,
                    saved_at,
                    status
                FROM saved_messages;
                """
            )
            row = await cursor.fetchone()

        self.assertTrue(was_inserted)
        self.assertEqual(
            row[:9],
            (
                "user-1",
                "message-1",
                None,
                "channel-1",
                "author-1",
                "Author One",
                "Saved content",
                "https://example.com/message-1",
                "2026-07-23T00:00:00+00:00",
            ),
        )
        self.assertIsNotNone(row[9])
        self.assertEqual(row[10], "UNREAD")

    async def test_duplicate_is_scoped_by_saver_and_message_id(self) -> None:
        first_insert, _ = await self.save_message(message_id="message-1")
        duplicate_insert, _ = await self.save_message(message_id="message-1")
        other_user_insert, _ = await self.save_message(
            saved_by_user_id="user-2",
            message_id="message-1",
        )

        async with aiosqlite.connect(database.DATABASE_PATH) as connection:
            cursor = await connection.execute(
                "SELECT COUNT(*) FROM saved_messages;"
            )
            row = await cursor.fetchone()

        self.assertTrue(first_insert)
        self.assertFalse(duplicate_insert)
        self.assertTrue(other_user_insert)
        self.assertEqual(row[0], 2)

    async def test_get_saved_messages_filters_orders_and_paginates(self) -> None:
        record_ids = {}

        for number in range(1, 8):
            _, record_ids[number] = await self.save_message(
                message_id=f"message-{number}",
            )

        await self.save_message(
            saved_by_user_id="user-2",
            message_id="other-user-message",
        )

        for number in (3, 6):
            await database.update_saved_message_status(
                record_id=record_ids[number],
                saved_by_user_id="user-1",
                status="READ_KEEP",
            )

        unread_page = await database.get_saved_messages(
            saved_by_user_id="user-1",
            status="UNREAD",
            limit=2,
            offset=1,
        )
        read_keep = await database.get_saved_messages(
            saved_by_user_id="user-1",
            status="READ_KEEP",
            limit=10,
            offset=0,
        )
        all_records = await database.get_saved_messages(
            saved_by_user_id="user-1",
            status="ALL",
            limit=3,
            offset=0,
        )

        self.assertEqual(
            [row["content"] for row in unread_page],
            ["Content for message-5", "Content for message-4"],
        )
        self.assertEqual(
            [row["content"] for row in read_keep],
            ["Content for message-6", "Content for message-3"],
        )
        self.assertEqual(
            [row["content"] for row in all_records],
            [
                "Content for message-7",
                "Content for message-6",
                "Content for message-5",
            ],
        )
        self.assertEqual(
            set(all_records[0].keys()),
            {
                "id",
                "author_name",
                "content",
                "jump_url",
                "message_created_at",
                "status",
            },
        )

    async def test_count_saved_messages_filters_status_and_owner(self) -> None:
        _, first_record_id = await self.save_message(message_id="message-1")
        await self.save_message(message_id="message-2")
        await self.save_message(
            saved_by_user_id="user-2",
            message_id="message-3",
        )
        await database.update_saved_message_status(
            record_id=first_record_id,
            saved_by_user_id="user-1",
            status="READ_KEEP",
        )

        unread_count = await database.count_saved_messages(
            saved_by_user_id="user-1",
        )
        read_keep_count = await database.count_saved_messages(
            saved_by_user_id="user-1",
            status="READ_KEEP",
        )
        all_count = await database.count_saved_messages(
            saved_by_user_id="user-1",
            status="ALL",
        )

        self.assertEqual(unread_count, 1)
        self.assertEqual(read_keep_count, 1)
        self.assertEqual(all_count, 2)

    async def test_status_update_validates_value_record_and_owner(self) -> None:
        _, record_id = await self.save_message(message_id="message-1")

        wrong_owner_updated = await database.update_saved_message_status(
            record_id=record_id,
            saved_by_user_id="user-2",
            status="READ_KEEP",
        )
        missing_record_updated = await database.update_saved_message_status(
            record_id=record_id + 100,
            saved_by_user_id="user-1",
            status="READ_KEEP",
        )
        correct_owner_updated = await database.update_saved_message_status(
            record_id=record_id,
            saved_by_user_id="user-1",
            status="READ_KEEP",
        )

        with self.assertRaisesRegex(ValueError, "Invalid status"):
            await database.update_saved_message_status(
                record_id=record_id,
                saved_by_user_id="user-1",
                status="READ",
            )

        rows = await database.get_saved_messages(
            saved_by_user_id="user-1",
            status="ALL",
        )

        self.assertFalse(wrong_owner_updated)
        self.assertFalse(missing_record_updated)
        self.assertTrue(correct_owner_updated)
        self.assertEqual(rows[0]["status"], "READ_KEEP")

    async def test_delete_requires_matching_record_owner(self) -> None:
        _, record_id = await self.save_message(message_id="message-1")

        wrong_owner_deleted = await database.delete_saved_message(
            record_id=record_id,
            saved_by_user_id="user-2",
        )
        record_after_wrong_owner = await database.count_saved_messages(
            saved_by_user_id="user-1",
            status="ALL",
        )
        correct_owner_deleted = await database.delete_saved_message(
            record_id=record_id,
            saved_by_user_id="user-1",
        )
        repeated_delete = await database.delete_saved_message(
            record_id=record_id,
            saved_by_user_id="user-1",
        )

        self.assertFalse(wrong_owner_deleted)
        self.assertEqual(record_after_wrong_owner, 1)
        self.assertTrue(correct_owner_deleted)
        self.assertFalse(repeated_delete)


if __name__ == "__main__":
    unittest.main()
