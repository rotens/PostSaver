import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord


os.environ.setdefault("DISCORD_TOKEN", "test-token")

with patch.object(discord.Client, "run"):
    import bot


class FakeAuthor:
    def __init__(self, user_id: int, name: str = "Author") -> None:
        self.id = user_id
        self.name = name
        self.mention = f"<@{user_id}>"

    def __str__(self) -> str:
        return self.name


class FakeMessage:
    def __init__(
        self,
        *,
        message_id: int = 100,
        author: FakeAuthor | None = None,
        guild_id: int | None = 10,
        channel_id: int = 20,
    ) -> None:
        self.id = message_id
        self.author = author or FakeAuthor(30)
        self.guild = (
            SimpleNamespace(id=guild_id)
            if guild_id is not None
            else None
        )
        self.channel = SimpleNamespace(id=channel_id)
        self.content = "Message content"
        self.jump_url = f"https://discord.test/{message_id}"
        self.created_at = datetime(2026, 7, 23, tzinfo=timezone.utc)


class FakeInteraction:
    def __init__(self, user_id: int = 42) -> None:
        self.user = SimpleNamespace(id=user_id)
        self.response = SimpleNamespace(
            send_message=AsyncMock(),
            send_modal=AsyncMock(),
        )


class SingleMessageCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_ignored_author_prevents_single_message_save(self) -> None:
        interaction = FakeInteraction()
        message = FakeMessage(author=FakeAuthor(30))

        with (
            patch.object(
                bot,
                "is_user_ignored",
                new=AsyncMock(return_value=True),
            ) as is_ignored,
            patch.object(
                bot,
                "save_unread_message",
                new=AsyncMock(),
            ) as save_message,
        ):
            await bot.save_as_unread.callback(interaction, message)

        is_ignored.assert_awaited_once_with(
            saved_by_user_id="42",
            ignored_user_id="30",
        )
        save_message.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once_with(
            (
                "Message not saved because you are ignoring "
                "messages from <@30>."
            ),
            ephemeral=True,
        )

    async def test_single_message_save_passes_metadata_and_reports_insert(
        self,
    ) -> None:
        interaction = FakeInteraction()
        message = FakeMessage()

        with (
            patch.object(
                bot,
                "is_user_ignored",
                new=AsyncMock(return_value=False),
            ),
            patch.object(
                bot,
                "save_unread_message",
                new=AsyncMock(return_value=True),
            ) as save_message,
        ):
            await bot.save_as_unread.callback(interaction, message)

        save_message.assert_awaited_once_with(
            saved_by_user_id="42",
            message_id="100",
            guild_id="10",
            channel_id="20",
            author_id="30",
            author_name="Author",
            content="Message content",
            jump_url="https://discord.test/100",
            message_created_at="2026-07-23T00:00:00+00:00",
        )
        interaction.response.send_message.assert_awaited_once_with(
            "Saved as UNREAD: https://discord.test/100",
            ephemeral=True,
        )

    async def test_single_message_duplicate_reports_existing_record(
        self,
    ) -> None:
        interaction = FakeInteraction()
        message = FakeMessage(guild_id=None)

        with (
            patch.object(
                bot,
                "is_user_ignored",
                new=AsyncMock(return_value=False),
            ),
            patch.object(
                bot,
                "save_unread_message",
                new=AsyncMock(return_value=False),
            ) as save_message,
        ):
            await bot.save_as_unread.callback(interaction, message)

        self.assertIsNone(save_message.await_args.kwargs["guild_id"])
        interaction.response.send_message.assert_awaited_once_with(
            "This message is already saved.",
            ephemeral=True,
        )


class IgnoreCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_ignore_helper_reports_new_and_existing_setting(self) -> None:
        user = FakeAuthor(30)
        first_interaction = FakeInteraction()
        second_interaction = FakeInteraction()

        with patch.object(
            bot,
            "ignore_user",
            new=AsyncMock(side_effect=[True, False]),
        ) as ignore_user:
            await bot.respond_to_ignore_user(first_interaction, user)
            await bot.respond_to_ignore_user(second_interaction, user)

        self.assertEqual(ignore_user.await_count, 2)
        ignore_user.assert_awaited_with(
            saved_by_user_id="42",
            ignored_user_id="30",
        )
        first_interaction.response.send_message.assert_awaited_once_with(
            "Messages from <@30> will now be ignored.",
            ephemeral=True,
        )
        second_interaction.response.send_message.assert_awaited_once_with(
            "Messages from <@30> are already ignored.",
            ephemeral=True,
        )

    async def test_unignore_helper_reports_removed_and_missing_setting(
        self,
    ) -> None:
        user = FakeAuthor(30)
        first_interaction = FakeInteraction()
        second_interaction = FakeInteraction()

        with patch.object(
            bot,
            "unignore_user",
            new=AsyncMock(side_effect=[True, False]),
        ) as unignore_user:
            await bot.respond_to_unignore_user(first_interaction, user)
            await bot.respond_to_unignore_user(second_interaction, user)

        self.assertEqual(unignore_user.await_count, 2)
        first_interaction.response.send_message.assert_awaited_once_with(
            "Messages from <@30> can now be saved again.",
            ephemeral=True,
        )
        second_interaction.response.send_message.assert_awaited_once_with(
            "Messages from <@30> were not being ignored.",
            ephemeral=True,
        )

    async def test_unignore_all_reports_zero_singular_and_plural(self) -> None:
        interactions = [FakeInteraction() for _ in range(3)]

        with patch.object(
            bot,
            "unignore_all_users",
            new=AsyncMock(side_effect=[0, 1, 2]),
        ):
            for interaction in interactions:
                await bot.unignore_all_user_messages.callback(interaction)

        expected_responses = [
            "Your ignore settings were already at the default.",
            "Reset your ignore settings for 1 user.",
            "Reset your ignore settings for 2 users.",
        ]

        for interaction, expected_response in zip(
            interactions,
            expected_responses,
        ):
            interaction.response.send_message.assert_awaited_once_with(
                expected_response,
                ephemeral=True,
            )


class RangeContextCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_set_range_start_stores_location_and_confirms(self) -> None:
        interaction = FakeInteraction()
        message = FakeMessage()

        with patch.object(
            bot,
            "set_pending_range_start",
            new=AsyncMock(),
        ) as set_start:
            await bot.set_range_start_context_menu.callback(
                interaction,
                message,
            )

        set_start.assert_awaited_once_with(
            saved_by_user_id="42",
            guild_id="10",
            channel_id="20",
            start_message_id="100",
        )
        interaction.response.send_message.assert_awaited_once_with(
            (
                "Range start set: https://discord.test/100\n"
                "Selecting another start will replace this one."
            ),
            ephemeral=True,
        )

    async def test_range_end_requires_pending_start(self) -> None:
        interaction = FakeInteraction()
        message = FakeMessage()

        with patch.object(
            bot,
            "get_pending_range",
            new=AsyncMock(return_value=None),
        ):
            await bot.save_through_range_end_context_menu.callback(
                interaction,
                message,
            )

        interaction.response.send_modal.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once_with(
            "Set a range start before selecting a range end.",
            ephemeral=True,
        )

    async def test_range_end_rejects_different_channel_before_modal(
        self,
    ) -> None:
        interaction = FakeInteraction()
        message = FakeMessage(channel_id=30)

        with patch.object(
            bot,
            "get_pending_range",
            new=AsyncMock(
                return_value={
                    "guild_id": "10",
                    "channel_id": "20",
                    "start_message_id": "90",
                }
            ),
        ):
            await bot.save_through_range_end_context_menu.callback(
                interaction,
                message,
            )

        interaction.response.send_modal.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once_with(
            "The range start and end must be in the same channel.",
            ephemeral=True,
        )

    async def test_valid_range_end_opens_owner_scoped_modal(self) -> None:
        interaction = FakeInteraction()
        message = FakeMessage()

        with patch.object(
            bot,
            "get_pending_range",
            new=AsyncMock(
                return_value={
                    "guild_id": "10",
                    "channel_id": "20",
                    "start_message_id": "90",
                }
            ),
        ):
            await bot.save_through_range_end_context_menu.callback(
                interaction,
                message,
            )

        interaction.response.send_message.assert_not_awaited()
        interaction.response.send_modal.assert_awaited_once()
        modal = interaction.response.send_modal.await_args.args[0]

        self.assertIsInstance(modal, bot.SaveRangeModal)
        self.assertEqual(modal.owner_user_id, 42)
        self.assertEqual(modal.expected_start_message_id, "90")
        self.assertIs(modal.end_message, message)


class BotConfigurationTests(unittest.TestCase):
    def test_message_content_intent_is_enabled(self) -> None:
        self.assertTrue(bot.bot.intents.message_content)


if __name__ == "__main__":
    unittest.main()
