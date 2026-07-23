import tempfile
import unittest
from pathlib import Path

import aiosqlite

import database


class RangeSavingDatabaseTests(unittest.IsolatedAsyncioTestCase):
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

    def message(
        self,
        message_id: str,
        position: int,
        *,
        content: str = "Message content",
    ) -> database.MessageToSave:
        return database.MessageToSave(
            message_id=message_id,
            guild_id="guild-1",
            channel_id="channel-1",
            author_id=f"author-{message_id}",
            author_name=f"Author {message_id}",
            content=content,
            jump_url=f"https://example.com/{message_id}",
            message_created_at="2026-07-22T00:00:00+00:00",
            position=position,
        )

    async def set_pending_start(
        self,
        *,
        start_message_id: str = "message-1",
    ) -> None:
        await database.set_pending_range_start(
            saved_by_user_id="user-1",
            guild_id="guild-1",
            channel_id="channel-1",
            start_message_id=start_message_id,
        )

    async def fetch_all(self, query: str) -> list[tuple]:
        async with aiosqlite.connect(database.DATABASE_PATH) as connection:
            cursor = await connection.execute(query)
            return await cursor.fetchall()

    async def test_range_save_creates_batch_messages_and_associations(
        self,
    ) -> None:
        await self.set_pending_start()

        result = await database.save_message_range_as_batch(
            saved_by_user_id="user-1",
            expected_start_message_id="message-1",
            title="  Useful discussion  ",
            messages=[
                self.message("message-1", 0),
                self.message("message-2", 1),
                self.message("message-3", 2),
            ],
        )

        batches = await self.fetch_all(
            "SELECT id, saved_by_user_id, title FROM saved_batches;"
        )
        saved_messages = await self.fetch_all(
            "SELECT message_id, status FROM saved_messages ORDER BY id;"
        )
        associations = await self.fetch_all(
            """
            SELECT saved_messages.message_id, saved_batch_messages.position
            FROM saved_batch_messages
            JOIN saved_messages
              ON saved_messages.id = saved_batch_messages.saved_message_id
            ORDER BY saved_batch_messages.position;
            """
        )
        pending_range = await database.get_pending_range(
            saved_by_user_id="user-1",
        )

        self.assertEqual(result.saved_count, 3)
        self.assertEqual(result.already_saved_count, 0)
        self.assertEqual(batches, [(result.batch_id, "user-1", "Useful discussion")])
        self.assertEqual(
            saved_messages,
            [
                ("message-1", "UNREAD"),
                ("message-2", "UNREAD"),
                ("message-3", "UNREAD"),
            ],
        )
        self.assertEqual(
            associations,
            [
                ("message-1", 0),
                ("message-2", 1),
                ("message-3", 2),
            ],
        )
        self.assertIsNone(pending_range)

    async def test_existing_read_keep_message_is_reused_without_resetting_status(
        self,
    ) -> None:
        await database.save_unread_message(
            saved_by_user_id="user-1",
            message_id="message-1",
            guild_id="guild-1",
            channel_id="channel-1",
            author_id="author-message-1",
            author_name="Author message-1",
            content="Existing content",
            jump_url="https://example.com/message-1",
            message_created_at="2026-07-22T00:00:00+00:00",
        )
        saved_rows = await self.fetch_all(
            "SELECT id FROM saved_messages WHERE message_id = 'message-1';"
        )
        await database.update_saved_message_status(
            record_id=saved_rows[0][0],
            saved_by_user_id="user-1",
            status="READ_KEEP",
        )
        await self.set_pending_start()

        result = await database.save_message_range_as_batch(
            saved_by_user_id="user-1",
            expected_start_message_id="message-1",
            title=None,
            messages=[
                self.message("message-1", 0),
                self.message("message-2", 1),
            ],
        )

        messages = await self.fetch_all(
            "SELECT message_id, status FROM saved_messages ORDER BY message_id;"
        )
        associations = await self.fetch_all(
            "SELECT position FROM saved_batch_messages ORDER BY position;"
        )

        self.assertEqual(result.saved_count, 1)
        self.assertEqual(result.already_saved_count, 1)
        self.assertEqual(
            messages,
            [
                ("message-1", "READ_KEEP"),
                ("message-2", "UNREAD"),
            ],
        )
        self.assertEqual(associations, [(0,), (1,)])

    async def test_stale_range_start_rejects_save_without_writes(self) -> None:
        await self.set_pending_start(start_message_id="newer-start")

        with self.assertRaises(database.PendingRangeChangedError):
            await database.save_message_range_as_batch(
                saved_by_user_id="user-1",
                expected_start_message_id="older-start",
                title="Should not exist",
                messages=[self.message("message-1", 0)],
            )

        batches = await self.fetch_all("SELECT id FROM saved_batches;")
        messages = await self.fetch_all("SELECT id FROM saved_messages;")
        pending_range = await database.get_pending_range(
            saved_by_user_id="user-1",
        )

        self.assertEqual(batches, [])
        self.assertEqual(messages, [])
        self.assertEqual(pending_range["start_message_id"], "newer-start")

    async def test_failure_rolls_back_batch_messages_and_pending_delete(
        self,
    ) -> None:
        await self.set_pending_start()
        invalid_message = self.message("message-2", 1, content=None)

        with self.assertRaises(RuntimeError):
            await database.save_message_range_as_batch(
                saved_by_user_id="user-1",
                expected_start_message_id="message-1",
                title="Rolled back",
                messages=[
                    self.message("message-1", 0),
                    invalid_message,
                ],
            )

        batches = await self.fetch_all("SELECT id FROM saved_batches;")
        messages = await self.fetch_all("SELECT id FROM saved_messages;")
        associations = await self.fetch_all(
            "SELECT batch_id FROM saved_batch_messages;"
        )
        pending_range = await database.get_pending_range(
            saved_by_user_id="user-1",
        )

        self.assertEqual(batches, [])
        self.assertEqual(messages, [])
        self.assertEqual(associations, [])
        self.assertEqual(pending_range["start_message_id"], "message-1")

    async def test_conditional_pending_delete_preserves_replaced_start(
        self,
    ) -> None:
        await self.set_pending_start(start_message_id="newer-start")

        wrong_start_deleted = await database.delete_pending_range_if_matches(
            saved_by_user_id="user-1",
            expected_start_message_id="older-start",
        )
        pending_range = await database.get_pending_range(
            saved_by_user_id="user-1",
        )
        correct_start_deleted = await database.delete_pending_range_if_matches(
            saved_by_user_id="user-1",
            expected_start_message_id="newer-start",
        )

        self.assertFalse(wrong_start_deleted)
        self.assertEqual(pending_range["start_message_id"], "newer-start")
        self.assertTrue(correct_start_deleted)

    async def test_get_ignored_user_ids_is_scoped_to_saver(self) -> None:
        await database.ignore_user(
            saved_by_user_id="user-1",
            ignored_user_id="author-1",
        )
        await database.ignore_user(
            saved_by_user_id="user-1",
            ignored_user_id="author-2",
        )
        await database.ignore_user(
            saved_by_user_id="user-2",
            ignored_user_id="author-3",
        )

        ignored_user_ids = await database.get_ignored_user_ids(
            saved_by_user_id="user-1",
        )

        self.assertEqual(ignored_user_ids, {"author-1", "author-2"})


if __name__ == "__main__":
    unittest.main()
