import tempfile
import unittest
from pathlib import Path

import database


class SavedAutocompleteQueryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_database_path = database.DATABASE_PATH
        database.DATABASE_PATH = (
            Path(self.temporary_directory.name) / "autocomplete.db"
        )
        await database.initialize_database()

    async def asyncTearDown(self) -> None:
        database.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    async def save_message(
        self,
        *,
        saver: str = "user-1",
        message_id: str,
        guild_id: str | None,
        guild_name: str | None,
        channel_id: str,
        channel_name: str | None,
        author_id: str,
        author_name: str,
    ) -> None:
        await database.save_unread_message(
            saved_by_user_id=saver,
            message_id=message_id,
            guild_id=guild_id,
            guild_name=guild_name,
            channel_id=channel_id,
            channel_name=channel_name,
            author_id=author_id,
            author_name=author_name,
            content=f"Content for {message_id}",
            jump_url=f"https://discord.test/{message_id}",
            message_created_at="2026-07-01T00:00:00+00:00",
        )

    async def test_author_choices_are_owner_scoped_and_use_latest_name(
        self,
    ) -> None:
        await self.save_message(
            message_id="1",
            guild_id="10",
            guild_name="Guild",
            channel_id="20",
            channel_name="general",
            author_id="30",
            author_name="Old Alice",
        )
        await self.save_message(
            message_id="2",
            guild_id="10",
            guild_name="Guild",
            channel_id="20",
            channel_name="general",
            author_id="30",
            author_name="New Alice",
        )
        await self.save_message(
            saver="user-2",
            message_id="3",
            guild_id="10",
            guild_name="Guild",
            channel_id="20",
            channel_name="general",
            author_id="99",
            author_name="Alice from another saver",
        )

        rows = await database.get_saved_author_autocomplete_choices(
            saved_by_user_id="user-1",
            current="alice",
        )

        self.assertEqual(
            [(row["author_id"], row["author_name"]) for row in rows],
            [("30", "New Alice")],
        )

    async def test_author_search_treats_like_characters_literally(
        self,
    ) -> None:
        await self.save_message(
            message_id="1",
            guild_id="10",
            guild_name="Guild",
            channel_id="20",
            channel_name="general",
            author_id="30",
            author_name="100%! Human",
        )
        await self.save_message(
            message_id="2",
            guild_id="10",
            guild_name="Guild",
            channel_id="20",
            channel_name="general",
            author_id="31",
            author_name="Ordinary Human",
        )

        rows = await database.get_saved_author_autocomplete_choices(
            saved_by_user_id="user-1",
            current="%",
        )
        escape_marker_rows = (
            await database.get_saved_author_autocomplete_choices(
                saved_by_user_id="user-1",
                current="!",
            )
        )

        self.assertEqual([row["author_id"] for row in rows], ["30"])
        self.assertEqual(
            [row["author_id"] for row in escape_marker_rows],
            ["30"],
        )

    async def test_channel_choices_can_be_limited_to_one_server(self) -> None:
        await self.save_message(
            message_id="1",
            guild_id="10",
            guild_name="First Guild",
            channel_id="20",
            channel_name="general",
            author_id="30",
            author_name="Alice",
        )
        await self.save_message(
            message_id="2",
            guild_id="11",
            guild_name="Second Guild",
            channel_id="21",
            channel_name="general-chat",
            author_id="31",
            author_name="Bob",
        )

        rows = await database.get_saved_channel_autocomplete_choices(
            saved_by_user_id="user-1",
            current="general",
            guild_id="10",
        )

        self.assertEqual([row["channel_id"] for row in rows], ["20"])
        self.assertEqual(rows[0]["guild_name"], "First Guild")

    async def test_guild_choices_exclude_direct_messages_and_validate_limit(
        self,
    ) -> None:
        await self.save_message(
            message_id="1",
            guild_id="10",
            guild_name="Test Guild",
            channel_id="20",
            channel_name="general",
            author_id="30",
            author_name="Alice",
        )
        await self.save_message(
            message_id="2",
            guild_id=None,
            guild_name=None,
            channel_id="99",
            channel_name="Direct Message with Bob",
            author_id="31",
            author_name="Bob",
        )

        rows = await database.get_saved_guild_autocomplete_choices(
            saved_by_user_id="user-1",
            current="",
        )

        self.assertEqual([row["guild_id"] for row in rows], ["10"])

        for invalid_limit in (0, 26):
            with self.subTest(limit=invalid_limit):
                with self.assertRaises(ValueError):
                    await database.get_saved_guild_autocomplete_choices(
                        saved_by_user_id="user-1",
                        current="",
                        limit=invalid_limit,
                    )


if __name__ == "__main__":
    unittest.main()
