import sqlite3
import tempfile
import unittest
from pathlib import Path

import aiosqlite

import database


class SavedMessageAttachmentTests(unittest.IsolatedAsyncioTestCase):
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
        message_id: str = "message-1",
        attachments: tuple[database.AttachmentToSave, ...] = (),
    ) -> int:
        was_inserted = await database.save_unread_message(
            saved_by_user_id=saved_by_user_id,
            message_id=message_id,
            guild_id="guild-1",
            channel_id="channel-1",
            author_id="author-1",
            author_name="Author",
            content="Message content",
            jump_url=f"https://example.com/messages/{message_id}",
            message_created_at="2026-07-25T00:00:00+00:00",
            attachments=attachments,
        )
        self.assertTrue(was_inserted)

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

        return row[0]

    def attachment(
        self,
        attachment_id: str,
        position: int,
        **overrides,
    ) -> database.AttachmentToSave:
        values = {
            "attachment_id": attachment_id,
            "filename": f"{attachment_id}.png",
            "url": f"https://cdn.example.com/{attachment_id}",
            "proxy_url": f"https://proxy.example.com/{attachment_id}",
            "content_type": "image/png",
            "size": 1024,
            "description": f"Description for {attachment_id}",
            "width": 800,
            "height": 600,
            "position": position,
        }
        values.update(overrides)

        return database.AttachmentToSave(**values)

    async def test_single_message_save_stores_attachments_atomically(
        self,
    ) -> None:
        attachments = (
            self.attachment("attachment-1", 0),
            self.attachment(
                "attachment-2",
                1,
                content_type="application/pdf",
                description=None,
                width=None,
                height=None,
            ),
        )

        saved_message_id = await self.save_message(
            attachments=attachments,
        )
        result = await database.get_attachments_for_saved_messages(
            saved_by_user_id="user-1",
            saved_message_ids=[saved_message_id],
        )

        self.assertEqual(
            [
                row["attachment_id"]
                for row in result[saved_message_id]
            ],
            ["attachment-1", "attachment-2"],
        )

    async def test_duplicate_message_save_backfills_attachments(
        self,
    ) -> None:
        saved_message_id = await self.save_message()
        await database.update_saved_message_status(
            record_id=saved_message_id,
            saved_by_user_id="user-1",
            status="READ_KEEP",
        )

        was_inserted = await database.save_unread_message(
            saved_by_user_id="user-1",
            message_id="message-1",
            guild_id="guild-1",
            channel_id="channel-1",
            author_id="author-1",
            author_name="Author",
            content="Updated content is not stored",
            jump_url="https://example.com/messages/message-1",
            message_created_at="2026-07-25T00:00:00+00:00",
            attachments=(self.attachment("attachment-1", 0),),
        )
        result = await database.get_attachments_for_saved_messages(
            saved_by_user_id="user-1",
            saved_message_ids=[saved_message_id],
        )

        async with aiosqlite.connect(database.DATABASE_PATH) as connection:
            cursor = await connection.execute(
                "SELECT content, status FROM saved_messages WHERE id = ?;",
                (saved_message_id,),
            )
            saved_message = await cursor.fetchone()

        self.assertFalse(was_inserted)
        self.assertEqual(saved_message, ("Message content", "READ_KEEP"))
        self.assertEqual(
            [row["attachment_id"] for row in result[saved_message_id]],
            ["attachment-1"],
        )

    async def test_attachment_failure_rolls_back_single_message_save(
        self,
    ) -> None:
        invalid_attachment = self.attachment(
            "attachment-1",
            0,
            filename=None,
        )

        with self.assertRaises(sqlite3.IntegrityError):
            await database.save_unread_message(
                saved_by_user_id="user-1",
                message_id="message-1",
                guild_id="guild-1",
                channel_id="channel-1",
                author_id="author-1",
                author_name="Author",
                content="Message content",
                jump_url="https://example.com/messages/message-1",
                message_created_at="2026-07-25T00:00:00+00:00",
                attachments=(invalid_attachment,),
            )

        async with aiosqlite.connect(database.DATABASE_PATH) as connection:
            cursor = await connection.execute(
                "SELECT COUNT(*) FROM saved_messages;"
            )
            saved_message_count = (await cursor.fetchone())[0]

        self.assertEqual(saved_message_count, 0)

    async def test_range_save_stores_each_messages_attachments(
        self,
    ) -> None:
        await database.set_pending_range_start(
            saved_by_user_id="user-1",
            guild_id="guild-1",
            channel_id="channel-1",
            start_message_id="message-1",
        )
        messages = [
            database.MessageToSave(
                message_id=f"message-{position + 1}",
                guild_id="guild-1",
                channel_id="channel-1",
                author_id="author-1",
                author_name="Author",
                content="Message content",
                jump_url=f"https://example.com/messages/{position + 1}",
                message_created_at="2026-07-25T00:00:00+00:00",
                position=position,
                attachments=(
                    self.attachment(
                        f"attachment-{position + 1}",
                        0,
                    ),
                ),
            )
            for position in range(2)
        ]

        result = await database.save_message_range_as_batch(
            saved_by_user_id="user-1",
            expected_start_message_id="message-1",
            title="Attachments",
            messages=messages,
        )

        async with aiosqlite.connect(database.DATABASE_PATH) as connection:
            cursor = await connection.execute(
                """
                SELECT saved_message.id, saved_message.message_id
                FROM saved_messages AS saved_message
                ORDER BY saved_message.id;
                """
            )
            saved_messages = await cursor.fetchall()

        attachments_by_message = (
            await database.get_attachments_for_saved_messages(
                saved_by_user_id="user-1",
                saved_message_ids=[row[0] for row in saved_messages],
            )
        )

        self.assertEqual(result.saved_count, 2)
        self.assertEqual(
            [
                attachments_by_message[saved_message_id][0]["attachment_id"]
                for saved_message_id, _ in saved_messages
            ],
            ["attachment-1", "attachment-2"],
        )
        self.assertIsNone(
            await database.get_pending_range(saved_by_user_id="user-1")
        )

    async def test_attachment_conflict_rolls_back_entire_range_save(
        self,
    ) -> None:
        saved_message_id = await self.save_message(
            attachments=(self.attachment("existing", 0),),
        )
        await database.set_pending_range_start(
            saved_by_user_id="user-1",
            guild_id="guild-1",
            channel_id="channel-1",
            start_message_id="message-1",
        )
        message = database.MessageToSave(
            message_id="message-1",
            guild_id="guild-1",
            channel_id="channel-1",
            author_id="author-1",
            author_name="Author",
            content="Message content",
            jump_url="https://example.com/messages/message-1",
            message_created_at="2026-07-25T00:00:00+00:00",
            position=0,
            attachments=(self.attachment("position-conflict", 0),),
        )

        with self.assertRaises(sqlite3.IntegrityError):
            await database.save_message_range_as_batch(
                saved_by_user_id="user-1",
                expected_start_message_id="message-1",
                title="Must roll back",
                messages=[message],
            )

        async with aiosqlite.connect(database.DATABASE_PATH) as connection:
            cursor = await connection.execute(
                "SELECT COUNT(*) FROM saved_batches;"
            )
            batch_count = (await cursor.fetchone())[0]
            cursor = await connection.execute(
                "SELECT COUNT(*) FROM saved_batch_messages;"
            )
            association_count = (await cursor.fetchone())[0]

        attachments = await database.get_attachments_for_saved_messages(
            saved_by_user_id="user-1",
            saved_message_ids=[saved_message_id],
        )
        pending_range = await database.get_pending_range(
            saved_by_user_id="user-1",
        )

        self.assertEqual(batch_count, 0)
        self.assertEqual(association_count, 0)
        self.assertEqual(
            [row["attachment_id"] for row in attachments[saved_message_id]],
            ["existing"],
        )
        self.assertEqual(pending_range["start_message_id"], "message-1")

    async def test_initialize_database_creates_attachment_table(
        self,
    ) -> None:
        async with aiosqlite.connect(database.DATABASE_PATH) as connection:
            cursor = await connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                  AND name = 'saved_message_attachments';
                """
            )
            table_row = await cursor.fetchone()

            cursor = await connection.execute(
                "PRAGMA foreign_key_list(saved_message_attachments);"
            )
            foreign_keys = await cursor.fetchall()

        self.assertEqual(table_row, ("saved_message_attachments",))
        self.assertEqual(len(foreign_keys), 1)
        self.assertEqual(foreign_keys[0][2], "saved_messages")
        self.assertEqual(foreign_keys[0][3], "saved_message_id")
        self.assertEqual(foreign_keys[0][6], "CASCADE")

    async def test_save_and_get_attachments_preserves_metadata_and_order(
        self,
    ) -> None:
        saved_message_id = await self.save_message()
        later_attachment = self.attachment(
            "attachment-2",
            1,
            content_type=None,
            description=None,
            width=None,
            height=None,
        )
        first_attachment = self.attachment("attachment-1", 0)

        inserted_count = await database.save_saved_message_attachments(
            saved_message_id=saved_message_id,
            saved_by_user_id="user-1",
            attachments=[
                later_attachment,
                first_attachment,
            ],
        )
        result = await database.get_attachments_for_saved_messages(
            saved_by_user_id="user-1",
            saved_message_ids=[
                saved_message_id,
                saved_message_id,
                999,
            ],
        )

        self.assertEqual(inserted_count, 2)
        self.assertEqual(set(result), {saved_message_id, 999})
        self.assertEqual(result[999], [])
        self.assertEqual(
            [
                row["attachment_id"]
                for row in result[saved_message_id]
            ],
            [
                "attachment-1",
                "attachment-2",
            ],
        )

        first_row, second_row = result[saved_message_id]
        self.assertEqual(first_row["filename"], "attachment-1.png")
        self.assertEqual(
            first_row["url"],
            "https://cdn.example.com/attachment-1",
        )
        self.assertEqual(
            first_row["proxy_url"],
            "https://proxy.example.com/attachment-1",
        )
        self.assertEqual(first_row["content_type"], "image/png")
        self.assertEqual(first_row["size"], 1024)
        self.assertEqual(
            first_row["description"],
            "Description for attachment-1",
        )
        self.assertEqual(first_row["width"], 800)
        self.assertEqual(first_row["height"], 600)
        self.assertEqual(first_row["position"], 0)
        self.assertIsNone(second_row["content_type"])
        self.assertIsNone(second_row["description"])
        self.assertIsNone(second_row["width"])
        self.assertIsNone(second_row["height"])

    async def test_duplicate_attachment_is_ignored(self) -> None:
        saved_message_id = await self.save_message()
        attachment = self.attachment("attachment-1", 0)

        first_count = await database.save_saved_message_attachments(
            saved_message_id=saved_message_id,
            saved_by_user_id="user-1",
            attachments=[attachment],
        )
        duplicate_count = await database.save_saved_message_attachments(
            saved_message_id=saved_message_id,
            saved_by_user_id="user-1",
            attachments=[attachment],
        )

        self.assertEqual(first_count, 1)
        self.assertEqual(duplicate_count, 0)

    async def test_attachment_ids_are_scoped_to_saved_message(self) -> None:
        first_message_id = await self.save_message(message_id="message-1")
        second_message_id = await self.save_message(message_id="message-2")
        attachment = self.attachment("shared-attachment", 0)

        first_count = await database.save_saved_message_attachments(
            saved_message_id=first_message_id,
            saved_by_user_id="user-1",
            attachments=[attachment],
        )
        second_count = await database.save_saved_message_attachments(
            saved_message_id=second_message_id,
            saved_by_user_id="user-1",
            attachments=[attachment],
        )

        self.assertEqual(first_count, 1)
        self.assertEqual(second_count, 1)

    async def test_save_and_get_require_message_ownership(self) -> None:
        first_user_message_id = await self.save_message(
            saved_by_user_id="user-1",
            message_id="message-1",
        )
        second_user_message_id = await self.save_message(
            saved_by_user_id="user-2",
            message_id="message-2",
        )
        attachment = self.attachment("attachment-1", 0)

        wrong_owner_count = await database.save_saved_message_attachments(
            saved_message_id=first_user_message_id,
            saved_by_user_id="user-2",
            attachments=[attachment],
        )
        correct_owner_count = await database.save_saved_message_attachments(
            saved_message_id=first_user_message_id,
            saved_by_user_id="user-1",
            attachments=[attachment],
        )
        second_user_result = (
            await database.get_attachments_for_saved_messages(
                saved_by_user_id="user-2",
                saved_message_ids=[
                    first_user_message_id,
                    second_user_message_id,
                ],
            )
        )

        self.assertEqual(wrong_owner_count, 0)
        self.assertEqual(correct_owner_count, 1)
        self.assertEqual(second_user_result[first_user_message_id], [])
        self.assertEqual(second_user_result[second_user_message_id], [])

    async def test_invalid_attachment_values_are_rejected(self) -> None:
        saved_message_id = await self.save_message()
        invalid_attachments = [
            (
                self.attachment("negative-position", -1),
                "positions cannot be negative",
            ),
            (
                self.attachment("negative-size", 0, size=-1),
                "sizes cannot be negative",
            ),
            (
                self.attachment("negative-width", 0, width=-1),
                "widths cannot be negative",
            ),
            (
                self.attachment("negative-height", 0, height=-1),
                "heights cannot be negative",
            ),
        ]

        for attachment, expected_message in invalid_attachments:
            with (
                self.subTest(attachment=attachment.attachment_id),
                self.assertRaisesRegex(ValueError, expected_message),
            ):
                await database.save_saved_message_attachments(
                    saved_message_id=saved_message_id,
                    saved_by_user_id="user-1",
                    attachments=[attachment],
                )

    async def test_duplicate_ids_and_positions_are_rejected(self) -> None:
        saved_message_id = await self.save_message()

        with self.assertRaisesRegex(ValueError, "IDs must be unique"):
            await database.save_saved_message_attachments(
                saved_message_id=saved_message_id,
                saved_by_user_id="user-1",
                attachments=[
                    self.attachment("attachment-1", 0),
                    self.attachment("attachment-1", 1),
                ],
            )

        with self.assertRaisesRegex(ValueError, "positions must be unique"):
            await database.save_saved_message_attachments(
                saved_message_id=saved_message_id,
                saved_by_user_id="user-1",
                attachments=[
                    self.attachment("attachment-1", 0),
                    self.attachment("attachment-2", 0),
                ],
            )

    async def test_position_conflict_rolls_back_the_whole_call(self) -> None:
        saved_message_id = await self.save_message()
        await database.save_saved_message_attachments(
            saved_message_id=saved_message_id,
            saved_by_user_id="user-1",
            attachments=[self.attachment("existing", 0)],
        )

        with self.assertRaises(sqlite3.IntegrityError):
            await database.save_saved_message_attachments(
                saved_message_id=saved_message_id,
                saved_by_user_id="user-1",
                attachments=[
                    self.attachment("would-be-inserted", 1),
                    self.attachment("position-conflict", 0),
                ],
            )

        result = await database.get_attachments_for_saved_messages(
            saved_by_user_id="user-1",
            saved_message_ids=[saved_message_id],
        )

        self.assertEqual(
            [
                row["attachment_id"]
                for row in result[saved_message_id]
            ],
            ["existing"],
        )

    async def test_deleting_saved_message_cascades_to_attachments(
        self,
    ) -> None:
        saved_message_id = await self.save_message()
        await database.save_saved_message_attachments(
            saved_message_id=saved_message_id,
            saved_by_user_id="user-1",
            attachments=[self.attachment("attachment-1", 0)],
        )

        was_deleted = await database.delete_saved_message(
            record_id=saved_message_id,
            saved_by_user_id="user-1",
        )

        async with aiosqlite.connect(database.DATABASE_PATH) as connection:
            cursor = await connection.execute(
                "SELECT COUNT(*) FROM saved_message_attachments;"
            )
            row = await cursor.fetchone()

        self.assertTrue(was_deleted)
        self.assertEqual(row[0], 0)

    async def test_empty_inputs_are_no_ops(self) -> None:
        inserted_count = await database.save_saved_message_attachments(
            saved_message_id=999,
            saved_by_user_id="user-1",
            attachments=[],
        )
        result = await database.get_attachments_for_saved_messages(
            saved_by_user_id="user-1",
            saved_message_ids=[],
        )

        self.assertEqual(inserted_count, 0)
        self.assertEqual(result, {})
