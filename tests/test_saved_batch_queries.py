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
        guild_id: str | None = "guild-1",
        guild_name: str | None = "Guild One",
        channel_id: str = "channel-1",
        channel_name: str | None = "general",
        author_id: str | None = None,
        author_name: str | None = None,
        content: str | None = None,
        message_created_at: str = "2026-07-24T00:00:00+00:00",
    ) -> int:
        resolved_author_id = author_id or f"author-{message_id}"
        resolved_author_name = author_name or f"Author {message_id}"
        resolved_content = content or f"Content for {message_id}"
        was_inserted = await database.save_unread_message(
            saved_by_user_id=saved_by_user_id,
            message_id=message_id,
            guild_id=guild_id,
            guild_name=guild_name,
            channel_id=channel_id,
            channel_name=channel_name,
            author_id=resolved_author_id,
            author_name=resolved_author_name,
            content=resolved_content,
            jump_url=f"https://example.com/{message_id}",
            message_created_at=message_created_at,
        )
        self.assertTrue(was_inserted)

        rows = await database.get_saved_messages(
            saved_by_user_id=saved_by_user_id,
            filters=database.SavedMessageFilters(status="ALL"),
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
        self.assertEqual(summary["total_message_count"], 0)
        self.assertEqual(summary["matching_message_count"], 0)
        self.assertEqual(summary["total_content_length"], 0)
        self.assertEqual(summary["matching_content_length"], 0)
        self.assertIsNone(summary["first_message_record_id"])
        self.assertIsNone(summary["first_message_guild_id"])
        self.assertIsNone(summary["first_message_guild_name"])
        self.assertIsNone(summary["first_message_channel_id"])
        self.assertIsNone(summary["first_message_channel_name"])
        self.assertIsNone(summary["first_message_author_name"])
        self.assertIsNone(summary["first_message_content"])
        self.assertIsNone(summary["first_message_jump_url"])
        self.assertIsNone(summary["first_message_created_at"])
        self.assertIsNone(summary["first_message_status"])
        self.assertIsNone(summary["last_message_record_id"])
        self.assertIsNone(summary["last_message_jump_url"])

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
            summary_before_delete["last_message_record_id"],
            first_message_id,
        )
        self.assertEqual(
            summary_before_delete["last_message_jump_url"],
            "https://example.com/message-1",
        )
        self.assertEqual(
            summary_before_delete["first_message_guild_id"],
            "guild-1",
        )
        self.assertEqual(
            summary_before_delete["first_message_guild_name"],
            "Guild One",
        )
        self.assertEqual(
            summary_before_delete["first_message_channel_id"],
            "channel-1",
        )
        self.assertEqual(
            summary_before_delete["first_message_channel_name"],
            "general",
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
        self.assertEqual(
            summary_after_delete["last_message_record_id"],
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
        self.assertEqual(first_page[0]["guild_id"], "guild-1")
        self.assertEqual(first_page[0]["guild_name"], "Guild One")
        self.assertEqual(first_page[0]["channel_id"], "channel-1")
        self.assertEqual(first_page[0]["channel_name"], "general")
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

    async def test_filtered_summaries_count_and_preview_matching_messages(
        self,
    ) -> None:
        first_message_id = await self.save_message(
            saved_by_user_id="user-1",
            message_id="first",
            author_id="other-author",
            content="Does not match",
            message_created_at="2026-07-10T00:00:00+00:00",
        )
        matching_message_id = await self.save_message(
            saved_by_user_id="user-1",
            message_id="matching",
            author_id="target-author",
            author_name="Target Author",
            content="Needle in message",
            message_created_at="2026-07-12T00:00:00+00:00",
        )
        later_matching_message_id = await self.save_message(
            saved_by_user_id="user-1",
            message_id="later-matching",
            author_id="target-author",
            content="Another needle",
            message_created_at="2026-07-13T00:00:00+00:00",
        )
        batch_id = await database.create_saved_batch(
            saved_by_user_id="user-1",
            title="Mixed batch",
        )
        await self.associate(
            batch_id=batch_id,
            saved_by_user_id="user-1",
            message_positions=[
                (first_message_id, 0),
                (matching_message_id, 1),
                (later_matching_message_id, 2),
            ],
        )
        filters = database.SavedMessageFilters(
            status="UNREAD",
            keyword="needle",
            created_from="2026-07-11T00:00:00+00:00",
            created_before="2026-07-14T00:00:00+00:00",
            author_id="target-author",
            channel_id="channel-1",
            guild_id="guild-1",
        )

        count = await database.count_saved_batches(
            saved_by_user_id="user-1",
            filters=filters,
        )
        summaries = await database.get_saved_batches(
            saved_by_user_id="user-1",
            filters=filters,
        )

        self.assertEqual(count, 1)
        self.assertEqual(len(summaries), 1)
        summary = summaries[0]
        self.assertEqual(summary["id"], batch_id)
        self.assertEqual(summary["total_message_count"], 3)
        self.assertEqual(summary["matching_message_count"], 2)
        self.assertEqual(
            summary["total_content_length"],
            len("Does not match")
            + len("Needle in message")
            + len("Another needle"),
        )
        self.assertEqual(
            summary["matching_content_length"],
            len("Needle in message") + len("Another needle"),
        )
        self.assertEqual(
            summary["first_message_record_id"],
            matching_message_id,
        )
        self.assertEqual(
            summary["first_message_content"],
            "Needle in message",
        )
        self.assertEqual(
            summary["last_message_record_id"],
            later_matching_message_id,
        )
        self.assertEqual(
            summary["last_message_jump_url"],
            "https://example.com/later-matching",
        )

    async def test_title_keyword_matches_messages_subject_to_other_filters(
        self,
    ) -> None:
        target_message_id = await self.save_message(
            saved_by_user_id="user-1",
            message_id="target",
            author_id="target-author",
            content="No keyword in this message",
        )
        wrong_author_message_id = await self.save_message(
            saved_by_user_id="user-1",
            message_id="wrong-author",
            author_id="other-author",
            content="No keyword here either",
        )
        batch_id = await database.create_saved_batch(
            saved_by_user_id="user-1",
            title="Python resources",
        )
        await self.associate(
            batch_id=batch_id,
            saved_by_user_id="user-1",
            message_positions=[
                (target_message_id, 0),
                (wrong_author_message_id, 1),
            ],
        )
        filters = database.SavedMessageFilters(
            status="ALL",
            keyword="PYTHON",
            author_id="target-author",
        )

        count = await database.count_saved_messages_in_batch(
            batch_id=batch_id,
            saved_by_user_id="user-1",
            filters=filters,
        )
        rows = await database.get_saved_messages_in_batch(
            batch_id=batch_id,
            saved_by_user_id="user-1",
            filters=filters,
        )

        self.assertEqual(count, 1)
        self.assertEqual([row["id"] for row in rows], [target_message_id])

    async def test_filtered_batch_queries_remain_owner_scoped(self) -> None:
        message_id = await self.save_message(
            saved_by_user_id="user-1",
            message_id="private",
            content="Secret needle",
        )
        batch_id = await database.create_saved_batch(
            saved_by_user_id="user-1",
            title="Private batch",
        )
        await self.associate(
            batch_id=batch_id,
            saved_by_user_id="user-1",
            message_positions=[(message_id, 0)],
        )
        filters = database.SavedMessageFilters(
            status="ALL",
            keyword="needle",
        )

        batch_count = await database.count_saved_batches(
            saved_by_user_id="user-2",
            filters=filters,
        )
        summaries = await database.get_saved_batches(
            saved_by_user_id="user-2",
            filters=filters,
        )
        detail_count = await database.count_saved_messages_in_batch(
            batch_id=batch_id,
            saved_by_user_id="user-2",
            filters=filters,
        )
        detail_rows = await database.get_saved_messages_in_batch(
            batch_id=batch_id,
            saved_by_user_id="user-2",
            filters=filters,
        )

        self.assertEqual(batch_count, 0)
        self.assertEqual(summaries, [])
        self.assertEqual(detail_count, 0)
        self.assertEqual(detail_rows, [])

    async def test_batch_date_and_length_sorting_is_deterministic(
        self,
    ) -> None:
        short_message_id = await self.save_message(
            saved_by_user_id="user-1",
            message_id="short",
            content="x",
        )
        medium_message_id = await self.save_message(
            saved_by_user_id="user-1",
            message_id="medium",
            content="yyyy",
        )
        long_message_id = await self.save_message(
            saved_by_user_id="user-1",
            message_id="long",
            content="zzzzzzzz",
        )
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
        await self.associate(
            batch_id=first_batch_id,
            saved_by_user_id="user-1",
            message_positions=[(medium_message_id, 0)],
        )
        await self.associate(
            batch_id=second_batch_id,
            saved_by_user_id="user-1",
            message_positions=[(short_message_id, 0)],
        )
        await self.associate(
            batch_id=third_batch_id,
            saved_by_user_id="user-1",
            message_positions=[(long_message_id, 0)],
        )

        date_desc = await database.get_saved_batches(
            saved_by_user_id="user-1",
            sort=database.SavedItemSort.DATE_DESC,
        )
        date_asc = await database.get_saved_batches(
            saved_by_user_id="user-1",
            sort=database.SavedItemSort.DATE_ASC,
        )
        length_desc_page_one = await database.get_saved_batches(
            saved_by_user_id="user-1",
            sort=database.SavedItemSort.LENGTH_DESC,
            limit=2,
            offset=0,
        )
        length_desc_page_two = await database.get_saved_batches(
            saved_by_user_id="user-1",
            sort=database.SavedItemSort.LENGTH_DESC,
            limit=2,
            offset=2,
        )
        length_asc = await database.get_saved_batches(
            saved_by_user_id="user-1",
            sort=database.SavedItemSort.LENGTH_ASC,
        )

        self.assertEqual(
            [row["id"] for row in date_desc],
            [third_batch_id, second_batch_id, first_batch_id],
        )
        self.assertEqual(
            [row["id"] for row in date_asc],
            [first_batch_id, second_batch_id, third_batch_id],
        )
        self.assertEqual(
            [
                row["id"]
                for row in [
                    *length_desc_page_one,
                    *length_desc_page_two,
                ]
            ],
            [third_batch_id, first_batch_id, second_batch_id],
        )
        self.assertEqual(
            [row["id"] for row in length_asc],
            [second_batch_id, first_batch_id, third_batch_id],
        )

    async def test_filtered_batch_length_sort_uses_matching_messages(
        self,
    ) -> None:
        first_matching_id = await self.save_message(
            saved_by_user_id="user-1",
            message_id="first-matching",
            author_id="target",
            content="xx",
        )
        first_nonmatching_id = await self.save_message(
            saved_by_user_id="user-1",
            message_id="first-other",
            author_id="other",
            content="y" * 100,
        )
        second_matching_id = await self.save_message(
            saved_by_user_id="user-1",
            message_id="second-matching",
            author_id="target",
            content="z" * 10,
        )
        first_batch_id = await database.create_saved_batch(
            saved_by_user_id="user-1",
            title="Large total, small match",
        )
        second_batch_id = await database.create_saved_batch(
            saved_by_user_id="user-1",
            title="Smaller total, large match",
        )
        await self.associate(
            batch_id=first_batch_id,
            saved_by_user_id="user-1",
            message_positions=[
                (first_matching_id, 0),
                (first_nonmatching_id, 1),
            ],
        )
        await self.associate(
            batch_id=second_batch_id,
            saved_by_user_id="user-1",
            message_positions=[(second_matching_id, 0)],
        )
        filters = database.SavedMessageFilters(
            status="ALL",
            author_id="target",
        )

        rows = await database.get_saved_batches(
            saved_by_user_id="user-1",
            filters=filters,
            sort=database.SavedItemSort.LENGTH_DESC,
        )

        self.assertEqual(
            [row["id"] for row in rows],
            [second_batch_id, first_batch_id],
        )
        self.assertEqual(rows[0]["matching_content_length"], 10)
        self.assertEqual(rows[1]["matching_content_length"], 2)
        self.assertGreater(
            rows[1]["total_content_length"],
            rows[0]["total_content_length"],
        )

    async def test_batch_sort_rejects_unvalidated_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid saved-item sort"):
            await database.get_saved_batches(
                saved_by_user_id="user-1",
                sort="NOT_A_SORT",
            )


if __name__ == "__main__":
    unittest.main()
