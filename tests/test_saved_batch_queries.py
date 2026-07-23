import tempfile
import unittest
from pathlib import Path

import database


class SavedBatchQueryTests(unittest.IsolatedAsyncioTestCase):
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
        saved_by_user_id: str,
        message_id: str,
    ) -> int:
        was_inserted = await database.save_unread_message(
            saved_by_user_id=saved_by_user_id,
            message_id=message_id,
            guild_id="guild-1",
            channel_id="channel-1",
            author_id=f"author-{message_id}",
            author_name=f"Author {message_id}",
            content=f"Content for {message_id}",
            jump_url=f"https://example.com/{message_id}",
            message_created_at="2026-07-24T00:00:00+00:00",
        )
        self.assertTrue(was_inserted)

        rows = await database.get_saved_messages(
            saved_by_user_id=saved_by_user_id,
            status="ALL",
            limit=1,
        )

        return rows[0]["id"]

    async def associate(
        self,
        *,
        batch_id: int,
        saved_by_user_id: str,
        message_positions: list[tuple[int, int]],
    ) -> None:
        associated_count = await database.associate_saved_messages_with_batch(
            batch_id=batch_id,
            saved_by_user_id=saved_by_user_id,
            message_positions=message_positions,
        )
        self.assertEqual(associated_count, len(message_positions))

    async def test_count_saved_batches_is_scoped_to_owner(self) -> None:
        await database.create_saved_batch(
            saved_by_user_id="user-1",
            title="First",
        )
        await database.create_saved_batch(
            saved_by_user_id="user-1",
            title="Second",
        )
        await database.create_saved_batch(
            saved_by_user_id="user-2",
            title="Other user's batch",
        )

        first_user_count = await database.count_saved_batches(
            saved_by_user_id="user-1",
        )
        second_user_count = await database.count_saved_batches(
            saved_by_user_id="user-2",
        )
        missing_user_count = await database.count_saved_batches(
            saved_by_user_id="user-3",
        )

        self.assertEqual(first_user_count, 2)
        self.assertEqual(second_user_count, 1)
        self.assertEqual(missing_user_count, 0)

    async def test_batch_summaries_are_newest_first_and_paginated(
        self,
    ) -> None:
        first_batch_id = await database.create_saved_batch(
            saved_by_user_id="user-1",
            title="First",
        )
        second_batch_id = await database.create_saved_batch(
            saved_by_user_id="user-1",
            title="Second",
        )
        third_batch_id = await database.create_saved_batch(
            saved_by_user_id="user-1",
            title="Third",
        )
        await database.create_saved_batch(
            saved_by_user_id="user-2",
            title="Other user's batch",
        )

        first_page = await database.get_saved_batches(
            saved_by_user_id="user-1",
            limit=2,
            offset=0,
        )
        second_page = await database.get_saved_batches(
            saved_by_user_id="user-1",
            limit=2,
            offset=2,
        )

        self.assertEqual(
            [row["id"] for row in first_page],
            [third_batch_id, second_batch_id],
        )
        self.assertEqual(
            [row["title"] for row in first_page],
            ["Third", "Second"],
        )
        self.assertEqual(
            [row["id"] for row in second_page],
            [first_batch_id],
        )

    async def test_empty_batch_summary_has_zero_count_and_no_preview(
        self,
    ) -> None:
        batch_id = await database.create_saved_batch(
            saved_by_user_id="user-1",
            title=None,
        )

        summaries = await database.get_saved_batches(
            saved_by_user_id="user-1",
        )
        summary = summaries[0]

        self.assertEqual(summary["id"], batch_id)
        self.assertIsNone(summary["title"])
        self.assertIsNotNone(summary["created_at"])
        self.assertEqual(summary["message_count"], 0)
        self.assertIsNone(summary["first_message_record_id"])
        self.assertIsNone(summary["first_message_author_name"])
        self.assertIsNone(summary["first_message_content"])
        self.assertIsNone(summary["first_message_jump_url"])
        self.assertIsNone(summary["first_message_created_at"])
        self.assertIsNone(summary["first_message_status"])

    async def test_summary_uses_lowest_remaining_batch_position(
        self,
    ) -> None:
        first_message_id = await self.save_message(
            saved_by_user_id="user-1",
            message_id="message-1",
        )
        second_message_id = await self.save_message(
            saved_by_user_id="user-1",
            message_id="message-2",
        )
        batch_id = await database.create_saved_batch(
            saved_by_user_id="user-1",
            title="Position test",
        )
        await self.associate(
            batch_id=batch_id,
            saved_by_user_id="user-1",
            message_positions=[
                (first_message_id, 5),
                (second_message_id, 2),
            ],
        )

        summary_before_delete = (
            await database.get_saved_batches(
                saved_by_user_id="user-1",
            )
        )[0]

        await database.delete_saved_message(
            record_id=second_message_id,
            saved_by_user_id="user-1",
        )

        summary_after_delete = (
            await database.get_saved_batches(
                saved_by_user_id="user-1",
            )
        )[0]

        self.assertEqual(summary_before_delete["message_count"], 2)
        self.assertEqual(
            summary_before_delete["first_message_record_id"],
            second_message_id,
        )
        self.assertEqual(
            summary_before_delete["first_message_author_name"],
            "Author message-2",
        )
        self.assertEqual(
            summary_before_delete["first_message_content"],
            "Content for message-2",
        )
        self.assertEqual(
            summary_before_delete["first_message_jump_url"],
            "https://example.com/message-2",
        )
        self.assertEqual(
            summary_before_delete["first_message_status"],
            "UNREAD",
        )
        self.assertEqual(summary_after_delete["message_count"], 1)
        self.assertEqual(
            summary_after_delete["first_message_record_id"],
            first_message_id,
        )

    async def test_batch_message_count_requires_matching_owner(self) -> None:
        message_ids = [
            await self.save_message(
                saved_by_user_id="user-1",
                message_id=f"message-{number}",
            )
            for number in range(1, 3)
        ]
        batch_id = await database.create_saved_batch(
            saved_by_user_id="user-1",
            title="Count test",
        )
        await self.associate(
            batch_id=batch_id,
            saved_by_user_id="user-1",
            message_positions=[
                (message_ids[0], 0),
                (message_ids[1], 1),
            ],
        )

        owner_count = await database.count_saved_messages_in_batch(
            batch_id=batch_id,
            saved_by_user_id="user-1",
        )
        wrong_owner_count = await database.count_saved_messages_in_batch(
            batch_id=batch_id,
            saved_by_user_id="user-2",
        )
        missing_batch_count = await database.count_saved_messages_in_batch(
            batch_id=batch_id + 100,
            saved_by_user_id="user-1",
        )

        self.assertEqual(owner_count, 2)
        self.assertEqual(wrong_owner_count, 0)
        self.assertEqual(missing_batch_count, 0)

    async def test_batch_messages_are_owner_scoped_ordered_and_paginated(
        self,
    ) -> None:
        message_ids = [
            await self.save_message(
                saved_by_user_id="user-1",
                message_id=f"message-{number}",
            )
            for number in range(1, 4)
        ]
        batch_id = await database.create_saved_batch(
            saved_by_user_id="user-1",
            title="Detail test",
        )
        await self.associate(
            batch_id=batch_id,
            saved_by_user_id="user-1",
            message_positions=[
                (message_ids[2], 20),
                (message_ids[0], 0),
                (message_ids[1], 10),
            ],
        )
        await database.update_saved_message_status(
            record_id=message_ids[1],
            saved_by_user_id="user-1",
            status="READ_KEEP",
        )

        first_page = await database.get_saved_messages_in_batch(
            batch_id=batch_id,
            saved_by_user_id="user-1",
            limit=2,
            offset=0,
        )
        second_page = await database.get_saved_messages_in_batch(
            batch_id=batch_id,
            saved_by_user_id="user-1",
            limit=2,
            offset=2,
        )
        wrong_owner_rows = await database.get_saved_messages_in_batch(
            batch_id=batch_id,
            saved_by_user_id="user-2",
        )

        self.assertEqual(
            [row["id"] for row in first_page],
            [message_ids[0], message_ids[1]],
        )
        self.assertEqual(
            [row["position"] for row in first_page],
            [0, 10],
        )
        self.assertEqual(first_page[1]["status"], "READ_KEEP")
        self.assertEqual(first_page[0]["author_name"], "Author message-1")
        self.assertEqual(first_page[0]["content"], "Content for message-1")
        self.assertEqual(
            first_page[0]["jump_url"],
            "https://example.com/message-1",
        )
        self.assertEqual(
            first_page[0]["message_created_at"],
            "2026-07-24T00:00:00+00:00",
        )
        self.assertEqual(
            [row["id"] for row in second_page],
            [message_ids[2]],
        )
        self.assertEqual(wrong_owner_rows, [])


if __name__ == "__main__":
    unittest.main()
