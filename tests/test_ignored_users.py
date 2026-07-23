import tempfile
import unittest
from pathlib import Path

import database


class IgnoredUserDatabaseTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_ignore_is_idempotent_and_scoped_to_saver(self) -> None:
        first_add = await database.ignore_user(
            saved_by_user_id="user-1",
            ignored_user_id="author-1",
        )
        duplicate_add = await database.ignore_user(
            saved_by_user_id="user-1",
            ignored_user_id="author-1",
        )
        other_saver_add = await database.ignore_user(
            saved_by_user_id="user-2",
            ignored_user_id="author-1",
        )

        first_saver_ignores = await database.is_user_ignored(
            saved_by_user_id="user-1",
            ignored_user_id="author-1",
        )
        unrelated_author_ignored = await database.is_user_ignored(
            saved_by_user_id="user-1",
            ignored_user_id="author-2",
        )

        self.assertTrue(first_add)
        self.assertFalse(duplicate_add)
        self.assertTrue(other_saver_add)
        self.assertTrue(first_saver_ignores)
        self.assertFalse(unrelated_author_ignored)

    async def test_self_ignore_is_supported(self) -> None:
        was_added = await database.ignore_user(
            saved_by_user_id="user-1",
            ignored_user_id="user-1",
        )
        is_ignored = await database.is_user_ignored(
            saved_by_user_id="user-1",
            ignored_user_id="user-1",
        )

        self.assertTrue(was_added)
        self.assertTrue(is_ignored)

    async def test_unignore_reports_whether_setting_existed(self) -> None:
        await database.ignore_user(
            saved_by_user_id="user-1",
            ignored_user_id="author-1",
        )

        wrong_saver_removed = await database.unignore_user(
            saved_by_user_id="user-2",
            ignored_user_id="author-1",
        )
        first_remove = await database.unignore_user(
            saved_by_user_id="user-1",
            ignored_user_id="author-1",
        )
        repeated_remove = await database.unignore_user(
            saved_by_user_id="user-1",
            ignored_user_id="author-1",
        )
        is_still_ignored = await database.is_user_ignored(
            saved_by_user_id="user-1",
            ignored_user_id="author-1",
        )

        self.assertFalse(wrong_saver_removed)
        self.assertTrue(first_remove)
        self.assertFalse(repeated_remove)
        self.assertFalse(is_still_ignored)

    async def test_unignore_all_only_resets_requesting_saver(self) -> None:
        for author_id in ("author-1", "author-2"):
            await database.ignore_user(
                saved_by_user_id="user-1",
                ignored_user_id=author_id,
            )

        await database.ignore_user(
            saved_by_user_id="user-2",
            ignored_user_id="author-3",
        )

        removed_count = await database.unignore_all_users(
            saved_by_user_id="user-1",
        )
        repeated_count = await database.unignore_all_users(
            saved_by_user_id="user-1",
        )
        first_saver_ignored_ids = await database.get_ignored_user_ids(
            saved_by_user_id="user-1",
        )
        second_saver_ignored_ids = await database.get_ignored_user_ids(
            saved_by_user_id="user-2",
        )

        self.assertEqual(removed_count, 2)
        self.assertEqual(repeated_count, 0)
        self.assertEqual(first_saver_ignored_ids, set())
        self.assertEqual(second_saver_ignored_ids, {"author-3"})


if __name__ == "__main__":
    unittest.main()
