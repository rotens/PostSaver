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
        channel_id: str = "channel-1",
        author_id: str | None = None,
        content: str | None = None,
        message_created_at: str = "2026-07-23T00:00:00+00:00",
    ) -> tuple[bool, int]:
        resolved_author_id = (
            author_id if author_id is not None else f"author-{message_id}"
        )
        resolved_content = (
            content if content is not None else f"Content for {message_id}"
        )
        was_inserted = await database.save_unread_message(
            saved_by_user_id=saved_by_user_id,
            message_id=message_id,
            guild_id=guild_id,
            guild_name="Guild One" if guild_id else None,
            channel_id=channel_id,
            channel_name="general",
            author_id=resolved_author_id,
            author_name=f"Author {message_id}",
            content=resolved_content,
            jump_url=f"https://example.com/{message_id}",
            message_created_at=message_created_at,
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
            guild_name=None,
            channel_id="channel-1",
            channel_name="Direct Message with Author One",
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
                    guild_name,
                    channel_id,
                    channel_name,
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
            row[:11],
            (
                "user-1",
                "message-1",
                None,
                None,
                "channel-1",
                "Direct Message with Author One",
                "author-1",
                "Author One",
                "Saved content",
                "https://example.com/message-1",
                "2026-07-23T00:00:00+00:00",
            ),
        )
        self.assertIsNotNone(row[11])
        self.assertEqual(row[12], "UNREAD")

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
            filters=database.SavedMessageFilters(status="UNREAD"),
            limit=2,
            offset=1,
        )
        read_keep = await database.get_saved_messages(
            saved_by_user_id="user-1",
            filters=database.SavedMessageFilters(status="READ_KEEP"),
            limit=10,
            offset=0,
        )
        all_records = await database.get_saved_messages(
            saved_by_user_id="user-1",
            filters=database.SavedMessageFilters(status="ALL"),
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
                "guild_id",
                "guild_name",
                "channel_id",
                "channel_name",
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
            filters=database.SavedMessageFilters(status="READ_KEEP"),
        )
        all_count = await database.count_saved_messages(
            saved_by_user_id="user-1",
            filters=database.SavedMessageFilters(status="ALL"),
        )

        self.assertEqual(unread_count, 1)
        self.assertEqual(read_keep_count, 1)
        self.assertEqual(all_count, 2)

    async def test_individual_saved_message_filters(self) -> None:
        await self.save_message(
            message_id="message-1",
            guild_id="guild-1",
            channel_id="channel-1",
            author_id="author-1",
            content="First Python note",
            message_created_at="2026-07-01T00:00:00+00:00",
        )
        await self.save_message(
            message_id="message-2",
            guild_id="guild-1",
            channel_id="channel-2",
            author_id="author-2",
            content="Python database discussion",
            message_created_at="2026-07-10T00:00:00+00:00",
        )
        await self.save_message(
            message_id="message-3",
            guild_id="guild-2",
            channel_id="channel-3",
            author_id="author-1",
            content="Unrelated topic",
            message_created_at="2026-07-20T00:00:00+00:00",
        )
        await self.save_message(
            saved_by_user_id="user-2",
            message_id="other-user-message",
            guild_id="guild-1",
            channel_id="channel-1",
            author_id="author-1",
            content="Python belonging to another saver",
            message_created_at="2026-07-10T00:00:00+00:00",
        )

        keyword_rows = await database.get_saved_messages(
            saved_by_user_id="user-1",
            filters=database.SavedMessageFilters(
                status="ALL",
                keyword="DATABASE",
            ),
        )
        author_rows = await database.get_saved_messages(
            saved_by_user_id="user-1",
            filters=database.SavedMessageFilters(
                status="ALL",
                author_id="author-1",
            ),
        )
        channel_rows = await database.get_saved_messages(
            saved_by_user_id="user-1",
            filters=database.SavedMessageFilters(
                status="ALL",
                channel_id="channel-2",
            ),
        )
        guild_rows = await database.get_saved_messages(
            saved_by_user_id="user-1",
            filters=database.SavedMessageFilters(
                status="ALL",
                guild_id="guild-1",
            ),
        )
        from_rows = await database.get_saved_messages(
            saved_by_user_id="user-1",
            filters=database.SavedMessageFilters(
                status="ALL",
                created_from="2026-07-10T00:00:00+00:00",
            ),
        )
        before_rows = await database.get_saved_messages(
            saved_by_user_id="user-1",
            filters=database.SavedMessageFilters(
                status="ALL",
                created_before="2026-07-20T00:00:00+00:00",
            ),
        )

        self.assertEqual(
            [row["content"] for row in keyword_rows],
            ["Python database discussion"],
        )
        self.assertEqual(
            [row["content"] for row in author_rows],
            ["Unrelated topic", "First Python note"],
        )
        self.assertEqual(
            [row["content"] for row in channel_rows],
            ["Python database discussion"],
        )
        self.assertEqual(
            [row["content"] for row in guild_rows],
            ["Python database discussion", "First Python note"],
        )
        self.assertEqual(
            [row["content"] for row in from_rows],
            ["Unrelated topic", "Python database discussion"],
        )
        self.assertEqual(
            [row["content"] for row in before_rows],
            ["Python database discussion", "First Python note"],
        )

    async def test_keyword_treats_like_characters_as_literal_text(
        self,
    ) -> None:
        await self.save_message(
            message_id="literal",
            content=r"Release 100%_READY from C:\Temp",
        )
        await self.save_message(
            message_id="wildcard-decoy",
            content="Release 100-anythingXREADY from elsewhere",
        )
        await self.save_message(
            message_id="escape-marker",
            content="Important! literal marker",
        )

        percent_and_underscore_rows = await database.get_saved_messages(
            saved_by_user_id="user-1",
            filters=database.SavedMessageFilters(
                status="ALL",
                keyword="100%_ready",
            ),
        )
        backslash_rows = await database.get_saved_messages(
            saved_by_user_id="user-1",
            filters=database.SavedMessageFilters(
                status="ALL",
                keyword=r"C:\Temp",
            ),
        )
        escape_marker_rows = await database.get_saved_messages(
            saved_by_user_id="user-1",
            filters=database.SavedMessageFilters(
                status="ALL",
                keyword="!",
            ),
        )

        self.assertEqual(
            [row["content"] for row in percent_and_underscore_rows],
            [r"Release 100%_READY from C:\Temp"],
        )
        self.assertEqual(
            [row["content"] for row in backslash_rows],
            [r"Release 100%_READY from C:\Temp"],
        )
        self.assertEqual(
            [row["content"] for row in escape_marker_rows],
            ["Important! literal marker"],
        )

    async def test_count_and_listing_share_combined_filters(self) -> None:
        _, target_id = await self.save_message(
            message_id="target",
            guild_id="guild-1",
            channel_id="channel-1",
            author_id="author-1",
            content="Python SQLite target",
            message_created_at="2026-07-10T00:00:00+00:00",
        )
        _, wrong_author_id = await self.save_message(
            message_id="wrong-author",
            guild_id="guild-1",
            channel_id="channel-1",
            author_id="author-2",
            content="Python SQLite wrong author",
            message_created_at="2026-07-10T00:00:00+00:00",
        )
        _, wrong_location_id = await self.save_message(
            message_id="wrong-location",
            guild_id="guild-2",
            channel_id="channel-2",
            author_id="author-1",
            content="Python SQLite wrong location",
            message_created_at="2026-07-10T00:00:00+00:00",
        )
        await self.save_message(
            message_id="wrong-status",
            guild_id="guild-1",
            channel_id="channel-1",
            author_id="author-1",
            content="Python SQLite still unread",
            message_created_at="2026-07-10T00:00:00+00:00",
        )

        for record_id in (target_id, wrong_author_id, wrong_location_id):
            await database.update_saved_message_status(
                record_id=record_id,
                saved_by_user_id="user-1",
                status="READ_KEEP",
            )

        filters = database.SavedMessageFilters(
            status="READ_KEEP",
            keyword="sqlite",
            created_from="2026-07-01T00:00:00+00:00",
            created_before="2026-07-15T00:00:00+00:00",
            author_id="author-1",
            channel_id="channel-1",
            guild_id="guild-1",
        )
        rows = await database.get_saved_messages(
            saved_by_user_id="user-1",
            filters=filters,
        )
        count = await database.count_saved_messages(
            saved_by_user_id="user-1",
            filters=filters,
        )

        self.assertEqual(count, 1)
        self.assertEqual(
            [row["content"] for row in rows],
            ["Python SQLite target"],
        )

    async def test_saved_message_date_and_length_sorting_is_deterministic(
        self,
    ) -> None:
        _, first_id = await self.save_message(
            message_id="first",
            content="xxxx",
            message_created_at="2026-07-02T00:00:00+00:00",
        )
        _, second_id = await self.save_message(
            message_id="second",
            content="y",
            message_created_at="2026-07-01T00:00:00+00:00",
        )
        _, third_id = await self.save_message(
            message_id="third",
            content="zzzz",
            message_created_at="2026-07-02T00:00:00+00:00",
        )
        filters = database.SavedMessageFilters(status="ALL")

        date_desc = await database.get_saved_messages(
            saved_by_user_id="user-1",
            filters=filters,
            sort=database.SavedItemSort.DATE_DESC,
        )
        date_asc = await database.get_saved_messages(
            saved_by_user_id="user-1",
            filters=filters,
            sort=database.SavedItemSort.DATE_ASC,
        )
        length_desc_page_one = await database.get_saved_messages(
            saved_by_user_id="user-1",
            filters=filters,
            sort=database.SavedItemSort.LENGTH_DESC,
            limit=2,
            offset=0,
        )
        length_desc_page_two = await database.get_saved_messages(
            saved_by_user_id="user-1",
            filters=filters,
            sort=database.SavedItemSort.LENGTH_DESC,
            limit=2,
            offset=2,
        )
        length_asc = await database.get_saved_messages(
            saved_by_user_id="user-1",
            filters=filters,
            sort=database.SavedItemSort.LENGTH_ASC,
        )

        self.assertEqual(
            [row["id"] for row in date_desc],
            [third_id, first_id, second_id],
        )
        self.assertEqual(
            [row["id"] for row in date_asc],
            [second_id, first_id, third_id],
        )
        self.assertEqual(
            [
                row["id"]
                for row in [
                    *length_desc_page_one,
                    *length_desc_page_two,
                ]
            ],
            [third_id, first_id, second_id],
        )
        self.assertEqual(
            [row["id"] for row in length_asc],
            [second_id, first_id, third_id],
        )

    async def test_saved_message_sort_rejects_unvalidated_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid saved-item sort"):
            await database.get_saved_messages(
                saved_by_user_id="user-1",
                sort="NOT_A_SORT",
            )

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
            filters=database.SavedMessageFilters(status="ALL"),
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
            filters=database.SavedMessageFilters(status="ALL"),
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
