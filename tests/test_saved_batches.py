import tempfile
import unittest
from pathlib import Path

import aiosqlite

import database


class SavedBatchTests(unittest.IsolatedAsyncioTestCase):
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
            author_id="author-1",
            author_name="Author",
            content=f"Content for {message_id}",
            jump_url=f"https://example.com/{message_id}",
            message_created_at="2026-07-22T00:00:00+00:00",
        )
        self.assertTrue(was_inserted)

        query = """
        SELECT id
        FROM saved_messages
        WHERE saved_by_user_id = ?
          AND message_id = ?;
        """

        async with aiosqlite.connect(database.DATABASE_PATH) as connection:
            cursor = await connection.execute(
                query,
                (
                    saved_by_user_id,
                    message_id,
                ),
            )
            row = await cursor.fetchone()

        return row[0]

    async def test_initialize_database_creates_batch_tables(self) -> None:
        query = """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name IN ('saved_batches', 'saved_batch_messages')
        ORDER BY name;
        """

        async with aiosqlite.connect(database.DATABASE_PATH) as connection:
            cursor = await connection.execute(query)
            rows = await cursor.fetchall()

        self.assertEqual(
            rows,
            [
                ("saved_batch_messages",),
                ("saved_batches",),
            ],
        )

    async def test_create_saved_batch_stores_optional_normalized_title(
        self,
    ) -> None:
        titled_batch_id = await database.create_saved_batch(
            saved_by_user_id="user-1",
            title="  Database discussion  ",
        )
        untitled_batch_id = await database.create_saved_batch(
            saved_by_user_id="user-1",
            title="   ",
        )

        query = """
        SELECT id, saved_by_user_id, title, created_at
        FROM saved_batches
        ORDER BY id;
        """

        async with aiosqlite.connect(database.DATABASE_PATH) as connection:
            cursor = await connection.execute(query)
            rows = await cursor.fetchall()

        self.assertEqual(rows[0][0], titled_batch_id)
        self.assertEqual(rows[0][1], "user-1")
        self.assertEqual(rows[0][2], "Database discussion")
        self.assertIsNotNone(rows[0][3])
        self.assertEqual(rows[1][0], untitled_batch_id)
        self.assertIsNone(rows[1][2])

    async def test_delete_empty_batch_reports_first_and_repeated_delete(
        self,
    ) -> None:
        batch_id = await database.create_saved_batch(
            saved_by_user_id="user-1",
            title="Temporary batch",
        )

        first_delete = await database.delete_empty_saved_batch(
            batch_id=batch_id,
            saved_by_user_id="user-1",
        )
        repeated_delete = await database.delete_empty_saved_batch(
            batch_id=batch_id,
            saved_by_user_id="user-1",
        )

        self.assertTrue(first_delete)
        self.assertFalse(repeated_delete)

    async def test_delete_empty_batch_rejects_nonempty_batch(self) -> None:
        saved_message_id = await self.save_message(
            saved_by_user_id="user-1",
            message_id="message-1",
        )
        batch_id = await database.create_saved_batch(
            saved_by_user_id="user-1",
            title="Populated batch",
        )
        await database.associate_saved_messages_with_batch(
            batch_id=batch_id,
            saved_by_user_id="user-1",
            message_positions=[(saved_message_id, 0)],
        )

        was_deleted = await database.delete_empty_saved_batch(
            batch_id=batch_id,
            saved_by_user_id="user-1",
        )

        async with aiosqlite.connect(database.DATABASE_PATH) as connection:
            cursor = await connection.execute(
                "SELECT COUNT(*) FROM saved_batches WHERE id = ?;",
                (batch_id,),
            )
            batch_count = (await cursor.fetchone())[0]
            cursor = await connection.execute(
                "SELECT COUNT(*) FROM saved_messages WHERE id = ?;",
                (saved_message_id,),
            )
            message_count = (await cursor.fetchone())[0]

        self.assertFalse(was_deleted)
        self.assertEqual(batch_count, 1)
        self.assertEqual(message_count, 1)

    async def test_delete_empty_batch_requires_matching_owner(self) -> None:
        batch_id = await database.create_saved_batch(
            saved_by_user_id="user-1",
            title="Owned batch",
        )

        was_deleted = await database.delete_empty_saved_batch(
            batch_id=batch_id,
            saved_by_user_id="user-2",
        )

        async with aiosqlite.connect(database.DATABASE_PATH) as connection:
            cursor = await connection.execute(
                "SELECT saved_by_user_id FROM saved_batches WHERE id = ?;",
                (batch_id,),
            )
            row = await cursor.fetchone()

        self.assertFalse(was_deleted)
        self.assertEqual(row, ("user-1",))

    async def test_delete_saved_batch_removes_only_its_associations(
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
        await database.save_unread_message(
            saved_by_user_id="user-1",
            message_id="message-1",
            guild_id="guild-1",
            channel_id="channel-1",
            author_id="author-1",
            author_name="Author",
            content="Existing content is preserved",
            jump_url="https://example.com/message-1",
            message_created_at="2026-07-22T00:00:00+00:00",
            attachments=(
                database.AttachmentToSave(
                    attachment_id="attachment-1",
                    filename="diagram.png",
                    url="https://cdn.example.com/diagram.png",
                    proxy_url="https://proxy.example.com/diagram.png",
                    content_type="image/png",
                    size=1024,
                    description=None,
                    width=800,
                    height=600,
                    position=0,
                ),
            ),
        )
        deleted_batch_id = await database.create_saved_batch(
            saved_by_user_id="user-1",
            title="Delete this batch",
        )
        retained_batch_id = await database.create_saved_batch(
            saved_by_user_id="user-1",
            title="Keep this batch",
        )
        await database.associate_saved_messages_with_batch(
            batch_id=deleted_batch_id,
            saved_by_user_id="user-1",
            message_positions=[
                (first_message_id, 0),
                (second_message_id, 1),
            ],
        )
        await database.associate_saved_messages_with_batch(
            batch_id=retained_batch_id,
            saved_by_user_id="user-1",
            message_positions=[(first_message_id, 0)],
        )

        result = await database.delete_saved_batch(
            batch_id=deleted_batch_id,
            saved_by_user_id="user-1",
        )
        repeated_result = await database.delete_saved_batch(
            batch_id=deleted_batch_id,
            saved_by_user_id="user-1",
        )

        async with aiosqlite.connect(database.DATABASE_PATH) as connection:
            cursor = await connection.execute(
                "SELECT id FROM saved_batches ORDER BY id;"
            )
            batches = await cursor.fetchall()
            cursor = await connection.execute(
                """
                SELECT batch_id, saved_message_id, position
                FROM saved_batch_messages
                ORDER BY batch_id, position;
                """
            )
            associations = await cursor.fetchall()
            cursor = await connection.execute(
                "SELECT id FROM saved_messages ORDER BY id;"
            )
            saved_messages = await cursor.fetchall()
            cursor = await connection.execute(
                "SELECT attachment_id FROM saved_message_attachments;"
            )
            attachments = await cursor.fetchall()

        self.assertEqual(
            result,
            database.SavedBatchDeleteResult(
                batch_id=deleted_batch_id,
                associations_removed=2,
            ),
        )
        self.assertIsNone(repeated_result)
        self.assertEqual(batches, [(retained_batch_id,)])
        self.assertEqual(
            associations,
            [(retained_batch_id, first_message_id, 0)],
        )
        self.assertEqual(
            saved_messages,
            [(first_message_id,), (second_message_id,)],
        )
        self.assertEqual(attachments, [("attachment-1",)])

    async def test_delete_saved_batch_requires_matching_owner(self) -> None:
        saved_message_id = await self.save_message(
            saved_by_user_id="user-1",
            message_id="message-1",
        )
        batch_id = await database.create_saved_batch(
            saved_by_user_id="user-1",
            title="Owned batch",
        )
        await database.associate_saved_messages_with_batch(
            batch_id=batch_id,
            saved_by_user_id="user-1",
            message_positions=[(saved_message_id, 0)],
        )

        result = await database.delete_saved_batch(
            batch_id=batch_id,
            saved_by_user_id="user-2",
        )

        async with aiosqlite.connect(database.DATABASE_PATH) as connection:
            cursor = await connection.execute(
                "SELECT COUNT(*) FROM saved_batches WHERE id = ?;",
                (batch_id,),
            )
            batch_count = (await cursor.fetchone())[0]
            cursor = await connection.execute(
                """
                SELECT COUNT(*)
                FROM saved_batch_messages
                WHERE batch_id = ?;
                """,
                (batch_id,),
            )
            association_count = (await cursor.fetchone())[0]

        self.assertIsNone(result)
        self.assertEqual(batch_count, 1)
        self.assertEqual(association_count, 1)

    async def test_associate_messages_preserves_positions(self) -> None:
        message_ids = [
            await self.save_message(
                saved_by_user_id="user-1",
                message_id=f"message-{number}",
            )
            for number in range(1, 4)
        ]
        batch_id = await database.create_saved_batch(
            saved_by_user_id="user-1",
            title="Ordered range",
        )

        associated_count = await database.associate_saved_messages_with_batch(
            batch_id=batch_id,
            saved_by_user_id="user-1",
            message_positions=[
                (message_ids[2], 2),
                (message_ids[0], 0),
                (message_ids[1], 1),
            ],
        )

        query = """
        SELECT saved_message_id, position
        FROM saved_batch_messages
        WHERE batch_id = ?
        ORDER BY position;
        """

        async with aiosqlite.connect(database.DATABASE_PATH) as connection:
            cursor = await connection.execute(query, (batch_id,))
            rows = await cursor.fetchall()

        self.assertEqual(associated_count, 3)
        self.assertEqual(
            rows,
            [
                (message_ids[0], 0),
                (message_ids[1], 1),
                (message_ids[2], 2),
            ],
        )

    async def test_same_message_can_belong_to_overlapping_batches(self) -> None:
        saved_message_id = await self.save_message(
            saved_by_user_id="user-1",
            message_id="message-1",
        )
        first_batch_id = await database.create_saved_batch(
            saved_by_user_id="user-1",
            title="First range",
        )
        second_batch_id = await database.create_saved_batch(
            saved_by_user_id="user-1",
            title="Second range",
        )

        first_count = await database.associate_saved_messages_with_batch(
            batch_id=first_batch_id,
            saved_by_user_id="user-1",
            message_positions=[(saved_message_id, 0)],
        )
        second_count = await database.associate_saved_messages_with_batch(
            batch_id=second_batch_id,
            saved_by_user_id="user-1",
            message_positions=[(saved_message_id, 0)],
        )
        duplicate_count = await database.associate_saved_messages_with_batch(
            batch_id=first_batch_id,
            saved_by_user_id="user-1",
            message_positions=[(saved_message_id, 0)],
        )

        self.assertEqual(first_count, 1)
        self.assertEqual(second_count, 1)
        self.assertEqual(duplicate_count, 0)

    async def test_association_requires_same_owner(self) -> None:
        first_user_message_id = await self.save_message(
            saved_by_user_id="user-1",
            message_id="message-1",
        )
        second_user_message_id = await self.save_message(
            saved_by_user_id="user-2",
            message_id="message-2",
        )
        batch_id = await database.create_saved_batch(
            saved_by_user_id="user-1",
            title=None,
        )

        wrong_message_owner_count = (
            await database.associate_saved_messages_with_batch(
                batch_id=batch_id,
                saved_by_user_id="user-1",
                message_positions=[(second_user_message_id, 0)],
            )
        )
        wrong_batch_owner_count = (
            await database.associate_saved_messages_with_batch(
                batch_id=batch_id,
                saved_by_user_id="user-2",
                message_positions=[(first_user_message_id, 0)],
            )
        )

        self.assertEqual(wrong_message_owner_count, 0)
        self.assertEqual(wrong_batch_owner_count, 0)

    async def test_negative_position_is_rejected_before_inserting(self) -> None:
        saved_message_id = await self.save_message(
            saved_by_user_id="user-1",
            message_id="message-1",
        )
        batch_id = await database.create_saved_batch(
            saved_by_user_id="user-1",
            title=None,
        )

        with self.assertRaisesRegex(ValueError, "cannot be negative"):
            await database.associate_saved_messages_with_batch(
                batch_id=batch_id,
                saved_by_user_id="user-1",
                message_positions=[
                    (saved_message_id, 0),
                    (saved_message_id, -1),
                ],
            )

        query = """
        SELECT COUNT(*)
        FROM saved_batch_messages
        WHERE batch_id = ?;
        """

        async with aiosqlite.connect(database.DATABASE_PATH) as connection:
            cursor = await connection.execute(query, (batch_id,))
            row = await cursor.fetchone()

        self.assertEqual(row[0], 0)

    async def test_deleting_saved_message_removes_batch_association(self) -> None:
        saved_message_id = await self.save_message(
            saved_by_user_id="user-1",
            message_id="message-1",
        )
        batch_id = await database.create_saved_batch(
            saved_by_user_id="user-1",
            title=None,
        )
        await database.associate_saved_messages_with_batch(
            batch_id=batch_id,
            saved_by_user_id="user-1",
            message_positions=[(saved_message_id, 0)],
        )

        was_deleted = await database.delete_saved_message(
            record_id=saved_message_id,
            saved_by_user_id="user-1",
        )

        query = """
        SELECT COUNT(*)
        FROM saved_batch_messages
        WHERE batch_id = ?;
        """

        async with aiosqlite.connect(database.DATABASE_PATH) as connection:
            cursor = await connection.execute(query, (batch_id,))
            row = await cursor.fetchone()

        self.assertTrue(was_deleted)
        self.assertEqual(row[0], 0)


if __name__ == "__main__":
    unittest.main()
