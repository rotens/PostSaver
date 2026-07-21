import tempfile
import unittest
from pathlib import Path

import aiosqlite

import database


class PendingRangeTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_initialize_database_creates_pending_ranges_table(
        self,
    ) -> None:
        query = """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'pending_ranges';
        """

        async with aiosqlite.connect(database.DATABASE_PATH) as connection:
            cursor = await connection.execute(query)
            row = await cursor.fetchone()

        self.assertEqual(row, ("pending_ranges",))

    async def test_get_pending_range_returns_none_when_missing(self) -> None:
        pending_range = await database.get_pending_range(
            saved_by_user_id="user-1",
        )

        self.assertIsNone(pending_range)

    async def test_set_pending_range_stores_selected_message(self) -> None:
        await database.set_pending_range_start(
            saved_by_user_id="user-1",
            guild_id="guild-1",
            channel_id="channel-1",
            start_message_id="message-1",
        )

        pending_range = await database.get_pending_range(
            saved_by_user_id="user-1",
        )

        self.assertIsNotNone(pending_range)
        self.assertEqual(pending_range["saved_by_user_id"], "user-1")
        self.assertEqual(pending_range["guild_id"], "guild-1")
        self.assertEqual(pending_range["channel_id"], "channel-1")
        self.assertEqual(pending_range["start_message_id"], "message-1")
        self.assertIsNotNone(pending_range["created_at"])

    async def test_setting_another_start_replaces_existing_start(self) -> None:
        await database.set_pending_range_start(
            saved_by_user_id="user-1",
            guild_id="guild-1",
            channel_id="channel-1",
            start_message_id="message-1",
        )
        await database.set_pending_range_start(
            saved_by_user_id="user-1",
            guild_id="guild-2",
            channel_id="channel-2",
            start_message_id="message-2",
        )

        pending_range = await database.get_pending_range(
            saved_by_user_id="user-1",
        )

        self.assertEqual(pending_range["guild_id"], "guild-2")
        self.assertEqual(pending_range["channel_id"], "channel-2")
        self.assertEqual(pending_range["start_message_id"], "message-2")

        query = """
        SELECT COUNT(*)
        FROM pending_ranges
        WHERE saved_by_user_id = ?;
        """

        async with aiosqlite.connect(database.DATABASE_PATH) as connection:
            cursor = await connection.execute(query, ("user-1",))
            row = await cursor.fetchone()

        self.assertEqual(row[0], 1)

    async def test_users_have_separate_pending_ranges(self) -> None:
        await database.set_pending_range_start(
            saved_by_user_id="user-1",
            guild_id="guild-1",
            channel_id="channel-1",
            start_message_id="message-1",
        )
        await database.set_pending_range_start(
            saved_by_user_id="user-2",
            guild_id=None,
            channel_id="channel-2",
            start_message_id="message-2",
        )

        first_range = await database.get_pending_range(
            saved_by_user_id="user-1",
        )
        second_range = await database.get_pending_range(
            saved_by_user_id="user-2",
        )

        self.assertEqual(first_range["start_message_id"], "message-1")
        self.assertEqual(second_range["start_message_id"], "message-2")
        self.assertIsNone(second_range["guild_id"])

    async def test_delete_pending_range_reports_whether_it_existed(self) -> None:
        await database.set_pending_range_start(
            saved_by_user_id="user-1",
            guild_id="guild-1",
            channel_id="channel-1",
            start_message_id="message-1",
        )

        first_delete = await database.delete_pending_range(
            saved_by_user_id="user-1",
        )
        second_delete = await database.delete_pending_range(
            saved_by_user_id="user-1",
        )
        pending_range = await database.get_pending_range(
            saved_by_user_id="user-1",
        )

        self.assertTrue(first_delete)
        self.assertFalse(second_delete)
        self.assertIsNone(pending_range)


if __name__ == "__main__":
    unittest.main()
