import sqlite3
import tempfile
import unittest
from pathlib import Path

import aiosqlite

import database


class ManualBatchDatabaseTests(unittest.IsolatedAsyncioTestCase):
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
        *,
        created_at: str = "2026-08-01T12:00:00+00:00",
        attachments: tuple[database.AttachmentToSave, ...] = (),
    ) -> database.MessageToSave:
        return database.MessageToSave(
            message_id=message_id,
            guild_id="guild-1",
            guild_name="Guild One",
            channel_id="channel-1",
            channel_name="general",
            author_id=f"author-{message_id}",
            author_name=f"Author {message_id}",
            content=f"Content for {message_id}",
            jump_url=f"https://example.com/{message_id}",
            message_created_at=created_at,
            position=0,
            attachments=attachments,
        )

    def attachment(
        self,
        attachment_id: str,
        *,
        filename: str | None = None,
    ) -> database.AttachmentToSave:
        return database.AttachmentToSave(
            attachment_id=attachment_id,
            filename=filename or f"{attachment_id}.png",
            url=f"https://cdn.example.com/{attachment_id}",
            proxy_url=f"https://proxy.example.com/{attachment_id}",
            content_type="image/png",
            size=1024,
            description=None,
            width=800,
            height=600,
            position=0,
        )

    async def fetch_all(
        self,
        query: str,
        values: tuple = (),
    ) -> list[tuple]:
        async with aiosqlite.connect(database.DATABASE_PATH) as connection:
            cursor = await connection.execute(query, values)
            return await cursor.fetchall()

    async def test_add_new_messages_appends_in_manual_addition_order(
        self,
    ) -> None:
        batch_id = await database.create_saved_batch(
            saved_by_user_id="user-1",
            title="Reading order",
        )

        newer_result = await database.add_message_to_saved_batch(
            batch_id=batch_id,
            saved_by_user_id="user-1",
            message=self.message(
                "newer",
                created_at="2026-08-02T12:00:00+00:00",
                attachments=(self.attachment("image-1"),),
            ),
        )
        older_result = await database.add_message_to_saved_batch(
            batch_id=batch_id,
            saved_by_user_id="user-1",
            message=self.message(
                "older",
                created_at="2026-08-01T12:00:00+00:00",
            ),
        )

        associations = await self.fetch_all(
            """
            SELECT saved_message.message_id, batch_message.position
            FROM saved_batch_messages AS batch_message
            JOIN saved_messages AS saved_message
              ON saved_message.id = batch_message.saved_message_id
            WHERE batch_message.batch_id = ?
            ORDER BY batch_message.position;
            """,
            (batch_id,),
        )
        attachments = await self.fetch_all(
            "SELECT attachment_id FROM saved_message_attachments;"
        )

        self.assertTrue(newer_result.message_was_saved)
        self.assertTrue(newer_result.association_was_created)
        self.assertEqual(newer_result.position, 0)
        self.assertTrue(older_result.message_was_saved)
        self.assertEqual(older_result.position, 1)
        self.assertEqual(associations, [("newer", 0), ("older", 1)])
        self.assertEqual(attachments, [("image-1",)])

    async def test_existing_read_keep_is_reused_and_duplicate_is_no_op(
        self,
    ) -> None:
        batch_id = await database.create_saved_batch(
            saved_by_user_id="user-1",
            title="Existing records",
        )
        await database.save_unread_message(
            saved_by_user_id="user-1",
            message_id="message-1",
            guild_id="guild-1",
            channel_id="channel-1",
            author_id="author-1",
            author_name="Author",
            content="Original content",
            jump_url="https://example.com/message-1",
            message_created_at="2026-08-01T12:00:00+00:00",
        )
        saved_message_id = (
            await self.fetch_all(
                "SELECT id FROM saved_messages WHERE message_id = 'message-1';"
            )
        )[0][0]
        await database.update_saved_message_status(
            record_id=saved_message_id,
            saved_by_user_id="user-1",
            status="READ_KEEP",
        )

        first_result = await database.add_message_to_saved_batch(
            batch_id=batch_id,
            saved_by_user_id="user-1",
            message=self.message("message-1"),
        )
        duplicate_result = await database.add_message_to_saved_batch(
            batch_id=batch_id,
            saved_by_user_id="user-1",
            message=self.message("message-1"),
        )
        saved_message = await self.fetch_all(
            "SELECT content, status FROM saved_messages WHERE id = ?;",
            (saved_message_id,),
        )
        associations = await self.fetch_all(
            """
            SELECT position
            FROM saved_batch_messages
            WHERE batch_id = ?;
            """,
            (batch_id,),
        )

        self.assertFalse(first_result.message_was_saved)
        self.assertTrue(first_result.association_was_created)
        self.assertFalse(duplicate_result.message_was_saved)
        self.assertFalse(duplicate_result.association_was_created)
        self.assertEqual(duplicate_result.position, 0)
        self.assertEqual(saved_message, [("Original content", "READ_KEEP")])
        self.assertEqual(associations, [(0,)])

    async def test_wrong_owner_is_rejected_before_saving_message(self) -> None:
        batch_id = await database.create_saved_batch(
            saved_by_user_id="user-1",
            title="Private batch",
        )

        with self.assertRaises(database.SavedBatchNotFoundError):
            await database.add_message_to_saved_batch(
                batch_id=batch_id,
                saved_by_user_id="user-2",
                message=self.message("must-not-be-saved"),
            )

        saved_messages = await self.fetch_all(
            "SELECT id FROM saved_messages WHERE saved_by_user_id = 'user-2';"
        )
        self.assertEqual(saved_messages, [])

    async def test_create_batch_with_message_is_atomic_and_normalizes_title(
        self,
    ) -> None:
        result = await database.create_saved_batch_with_message(
            saved_by_user_id="user-1",
            title="  Manual collection  ",
            message=self.message("message-1"),
        )

        batches = await self.fetch_all(
            "SELECT id, title FROM saved_batches;"
        )
        associations = await self.fetch_all(
            "SELECT batch_id, saved_message_id, position "
            "FROM saved_batch_messages;"
        )

        self.assertEqual(batches, [(result.batch_id, "Manual collection")])
        self.assertEqual(
            associations,
            [(result.batch_id, result.saved_message_id, 0)],
        )
        self.assertTrue(result.message_was_saved)
        self.assertTrue(result.association_was_created)

    async def test_create_batch_with_message_rolls_back_on_attachment_error(
        self,
    ) -> None:
        invalid_attachment = self.attachment("broken")
        object.__setattr__(invalid_attachment, "filename", None)

        with self.assertRaises(sqlite3.IntegrityError):
            await database.create_saved_batch_with_message(
                saved_by_user_id="user-1",
                title="Must roll back",
                message=self.message(
                    "message-1",
                    attachments=(invalid_attachment,),
                ),
            )

        self.assertEqual(
            await self.fetch_all("SELECT id FROM saved_batches;"),
            [],
        )
        self.assertEqual(
            await self.fetch_all("SELECT id FROM saved_messages;"),
            [],
        )

    async def test_recent_batches_are_owned_counted_and_deterministic(
        self,
    ) -> None:
        first_id = await database.create_saved_batch(
            saved_by_user_id="user-1",
            title="First",
        )
        second_id = await database.create_saved_batch(
            saved_by_user_id="user-1",
            title="Second",
        )
        third_id = await database.create_saved_batch(
            saved_by_user_id="user-1",
            title=None,
        )
        await database.create_saved_batch(
            saved_by_user_id="user-2",
            title="Other user's batch",
        )
        await database.add_message_to_saved_batch(
            batch_id=second_id,
            saved_by_user_id="user-1",
            message=self.message("message-1"),
        )
        await database.add_message_to_saved_batch(
            batch_id=second_id,
            saved_by_user_id="user-1",
            message=self.message("message-2"),
        )

        async with aiosqlite.connect(database.DATABASE_PATH) as connection:
            await connection.execute(
                """
                UPDATE saved_batches
                SET created_at = '2026-08-01 12:00:00'
                WHERE saved_by_user_id = 'user-1';
                """
            )
            await connection.commit()

        rows = await database.get_recent_saved_batches(
            saved_by_user_id="user-1",
            limit=2,
        )

        self.assertEqual([row["id"] for row in rows], [third_id, second_id])
        self.assertEqual([row["message_count"] for row in rows], [0, 2])
        self.assertNotIn(first_id, [row["id"] for row in rows])

        for invalid_limit in (0, 26):
            with self.assertRaisesRegex(ValueError, "between 1 and 25"):
                await database.get_recent_saved_batches(
                    saved_by_user_id="user-1",
                    limit=invalid_limit,
                )


if __name__ == "__main__":
    unittest.main()
