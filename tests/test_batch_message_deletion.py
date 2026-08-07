import tempfile
import unittest
from pathlib import Path

import aiosqlite

import database


class BatchMessageDeletionTests(unittest.IsolatedAsyncioTestCase):
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
        message_id: str,
        *,
        saved_at: str,
        attachment_count: int = 0,
    ) -> int:
        attachments = tuple(
            database.AttachmentToSave(
                attachment_id=f"{message_id}-attachment-{position}",
                filename=f"file-{position}.txt",
                url=f"https://cdn.example/{message_id}/{position}",
                proxy_url=f"https://proxy.example/{message_id}/{position}",
                content_type="text/plain",
                size=10,
                description=None,
                width=None,
                height=None,
                position=position,
            )
            for position in range(attachment_count)
        )
        self.assertTrue(
            await database.save_unread_message(
                saved_by_user_id="owner",
                message_id=message_id,
                guild_id="guild",
                channel_id="channel",
                author_id="author",
                author_name="Author",
                content=message_id,
                jump_url=f"https://discord.example/{message_id}",
                message_created_at="2026-08-01T00:00:00+00:00",
                attachments=attachments,
            )
        )
        async with aiosqlite.connect(database.DATABASE_PATH) as connection:
            await connection.execute(
                "UPDATE saved_messages SET saved_at = ? WHERE message_id = ?;",
                (saved_at, message_id),
            )
            await connection.commit()
            cursor = await connection.execute(
                "SELECT id FROM saved_messages WHERE message_id = ?;",
                (message_id,),
            )
            return (await cursor.fetchone())[0]

    async def create_scenario(self) -> tuple[int, int, dict[str, int]]:
        message_ids = {
            "older": await self.save_message(
                "older", saved_at="2026-08-01 11:59:59", attachment_count=1
            ),
            "equal": await self.save_message(
                "equal", saved_at="2026-08-01 12:00:00", attachment_count=2
            ),
            "newer": await self.save_message(
                "newer", saved_at="2026-08-01 12:00:01", attachment_count=3
            ),
            "shared": await self.save_message(
                "shared", saved_at="2026-08-01 12:00:02", attachment_count=4
            ),
        }
        target_batch_id = await database.create_saved_batch(
            saved_by_user_id="owner", title="Target"
        )
        other_batch_id = await database.create_saved_batch(
            saved_by_user_id="owner", title="Other"
        )
        async with aiosqlite.connect(database.DATABASE_PATH) as connection:
            await connection.execute(
                "UPDATE saved_batches SET created_at = ? WHERE id = ?;",
                ("2026-08-01 12:00:00", target_batch_id),
            )
            await connection.commit()
        await database.associate_saved_messages_with_batch(
            batch_id=target_batch_id,
            saved_by_user_id="owner",
            message_positions=[
                (message_ids["older"], 0),
                (message_ids["equal"], 1),
                (message_ids["newer"], 2),
                (message_ids["shared"], 3),
            ],
        )
        await database.associate_saved_messages_with_batch(
            batch_id=other_batch_id,
            saved_by_user_id="owner",
            message_positions=[(message_ids["shared"], 0)],
        )
        return target_batch_id, other_batch_id, message_ids

    async def fetch_ids(self, table: str) -> list[int]:
        async with aiosqlite.connect(database.DATABASE_PATH) as connection:
            cursor = await connection.execute(f"SELECT id FROM {table} ORDER BY id;")
            return [row[0] for row in await cursor.fetchall()]

    async def test_preview_classifies_messages_and_attachment_impact(self) -> None:
        target_batch_id, _, message_ids = await self.create_scenario()

        preview = await database.get_saved_batch_message_delete_preview(
            batch_id=target_batch_id,
            saved_by_user_id="owner",
        )

        self.assertIsNotNone(preview)
        self.assertEqual(preview.title, "Target")
        self.assertEqual(preview.total_message_count, 4)
        self.assertEqual(preview.shared_message_count, 1)
        self.assertEqual(preview.older_unshared_message_count, 2)
        self.assertEqual(preview.newer_unshared_message_count, 1)
        self.assertEqual(preview.keep_older_attachment_delete_count, 3)
        self.assertEqual(preview.delete_all_attachment_delete_count, 6)
        equal_state = next(
            state
            for state in preview.message_states
            if state.saved_message_id == message_ids["equal"]
        )
        self.assertTrue(equal_state.is_older_or_equal)

    async def test_keep_older_deletes_only_newer_unshared_message(self) -> None:
        target_batch_id, other_batch_id, message_ids = await self.create_scenario()
        preview = await database.get_saved_batch_message_delete_preview(
            batch_id=target_batch_id, saved_by_user_id="owner"
        )

        result = await database.delete_saved_batch_with_unshared_messages(
            batch_id=target_batch_id,
            saved_by_user_id="owner",
            mode=database.UnsharedMessageDeleteMode.KEEP_OLDER,
            expected_message_states=preview.message_states,
        )

        self.assertEqual(result.associations_removed, 4)
        self.assertEqual(result.saved_messages_deleted, 1)
        self.assertEqual(result.attachments_deleted, 3)
        self.assertEqual(result.shared_messages_kept, 1)
        self.assertEqual(result.older_unshared_messages_kept, 2)
        self.assertEqual(await self.fetch_ids("saved_batches"), [other_batch_id])
        self.assertEqual(
            await self.fetch_ids("saved_messages"),
            [message_ids["older"], message_ids["equal"], message_ids["shared"]],
        )
        async with aiosqlite.connect(database.DATABASE_PATH) as connection:
            cursor = await connection.execute(
                "SELECT COUNT(*) FROM saved_message_attachments;"
            )
            self.assertEqual((await cursor.fetchone())[0], 7)

    async def test_delete_all_unshared_preserves_shared_message(self) -> None:
        target_batch_id, other_batch_id, message_ids = await self.create_scenario()
        preview = await database.get_saved_batch_message_delete_preview(
            batch_id=target_batch_id, saved_by_user_id="owner"
        )

        result = await database.delete_saved_batch_with_unshared_messages(
            batch_id=target_batch_id,
            saved_by_user_id="owner",
            mode=database.UnsharedMessageDeleteMode.DELETE_ALL,
            expected_message_states=preview.message_states,
        )

        self.assertEqual(result.saved_messages_deleted, 3)
        self.assertEqual(result.attachments_deleted, 6)
        self.assertEqual(result.shared_messages_kept, 1)
        self.assertEqual(result.older_unshared_messages_kept, 0)
        self.assertEqual(await self.fetch_ids("saved_batches"), [other_batch_id])
        self.assertEqual(
            await self.fetch_ids("saved_messages"),
            [message_ids["shared"]],
        )

    async def test_preview_and_delete_require_matching_owner(self) -> None:
        target_batch_id, _, _ = await self.create_scenario()

        preview = await database.get_saved_batch_message_delete_preview(
            batch_id=target_batch_id, saved_by_user_id="other-user"
        )
        result = await database.delete_saved_batch_with_unshared_messages(
            batch_id=target_batch_id,
            saved_by_user_id="other-user",
            mode=database.UnsharedMessageDeleteMode.DELETE_ALL,
            expected_message_states=(),
        )

        self.assertIsNone(preview)
        self.assertIsNone(result)
        self.assertIn(target_batch_id, await self.fetch_ids("saved_batches"))

    async def test_changed_membership_aborts_without_deleting(self) -> None:
        target_batch_id, _, message_ids = await self.create_scenario()
        preview = await database.get_saved_batch_message_delete_preview(
            batch_id=target_batch_id, saved_by_user_id="owner"
        )
        added_id = await self.save_message(
            "added-later", saved_at="2026-08-01 12:00:03"
        )
        await database.associate_saved_messages_with_batch(
            batch_id=target_batch_id,
            saved_by_user_id="owner",
            message_positions=[(added_id, 4)],
        )

        with self.assertRaises(database.SavedBatchContentsChangedError):
            await database.delete_saved_batch_with_unshared_messages(
                batch_id=target_batch_id,
                saved_by_user_id="owner",
                mode=database.UnsharedMessageDeleteMode.DELETE_ALL,
                expected_message_states=preview.message_states,
            )

        self.assertIn(target_batch_id, await self.fetch_ids("saved_batches"))
        self.assertEqual(len(await self.fetch_ids("saved_messages")), 5)
        self.assertIn(message_ids["newer"], await self.fetch_ids("saved_messages"))

    async def test_changed_sharing_state_aborts_without_deleting(self) -> None:
        target_batch_id, other_batch_id, message_ids = await self.create_scenario()
        preview = await database.get_saved_batch_message_delete_preview(
            batch_id=target_batch_id, saved_by_user_id="owner"
        )
        await database.associate_saved_messages_with_batch(
            batch_id=other_batch_id,
            saved_by_user_id="owner",
            message_positions=[(message_ids["newer"], 1)],
        )

        with self.assertRaises(database.SavedBatchContentsChangedError):
            await database.delete_saved_batch_with_unshared_messages(
                batch_id=target_batch_id,
                saved_by_user_id="owner",
                mode=database.UnsharedMessageDeleteMode.DELETE_ALL,
                expected_message_states=preview.message_states,
            )

        self.assertIn(target_batch_id, await self.fetch_ids("saved_batches"))
        self.assertEqual(len(await self.fetch_ids("saved_messages")), 4)

    async def test_changed_attachment_impact_aborts_without_deleting(self) -> None:
        target_batch_id, _, message_ids = await self.create_scenario()
        preview = await database.get_saved_batch_message_delete_preview(
            batch_id=target_batch_id, saved_by_user_id="owner"
        )
        async with aiosqlite.connect(database.DATABASE_PATH) as connection:
            await connection.execute(
                """
                INSERT INTO saved_message_attachments (
                    saved_message_id,
                    attachment_id,
                    filename,
                    url,
                    proxy_url,
                    content_type,
                    size,
                    position
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    message_ids["newer"],
                    "new-attachment",
                    "new.txt",
                    "https://cdn.example/new",
                    "https://proxy.example/new",
                    "text/plain",
                    10,
                    3,
                ),
            )
            await connection.commit()

        with self.assertRaises(database.SavedBatchContentsChangedError):
            await database.delete_saved_batch_with_unshared_messages(
                batch_id=target_batch_id,
                saved_by_user_id="owner",
                mode=database.UnsharedMessageDeleteMode.DELETE_ALL,
                expected_message_states=preview.message_states,
            )

        self.assertIn(target_batch_id, await self.fetch_ids("saved_batches"))
        self.assertEqual(len(await self.fetch_ids("saved_messages")), 4)


if __name__ == "__main__":
    unittest.main()
