import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord


os.environ.setdefault("DISCORD_TOKEN", "test-token")

with patch.object(discord.Client, "run"):
    import bot

from database import RangeSaveResult


class FakeAuthor:
    def __init__(self, author_id: int, name: str) -> None:
        self.id = author_id
        self.name = name

    def __str__(self) -> str:
        return self.name


class FakeChannel:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id
        self.history_messages = []
        self.history_arguments = None
        self.fetched_message = None

    def history(self, **kwargs):
        self.history_arguments = kwargs

        async def iterate_messages():
            for message in self.history_messages:
                yield message

        return iterate_messages()

    async def fetch_message(self, message_id: int):
        if self.fetched_message is None:
            raise AssertionError("No fetched message was configured")

        if self.fetched_message.id != message_id:
            raise AssertionError("The unexpected message was requested")

        return self.fetched_message


class FakeMessage:
    def __init__(
        self,
        message_id: int,
        *,
        channel: FakeChannel,
        author_id: int,
        content: str | None = None,
    ) -> None:
        self.id = message_id
        self.guild = SimpleNamespace(id=10)
        self.channel = channel
        self.author = FakeAuthor(author_id, f"Author {author_id}")
        self.content = content or f"Message {message_id}"
        self.jump_url = f"https://discord.test/{message_id}"
        self.created_at = datetime(2026, 7, 22, tzinfo=timezone.utc)


class FakeInteraction:
    def __init__(self, user_id: int = 42) -> None:
        self.user = SimpleNamespace(id=user_id)
        self.response = SimpleNamespace(defer=AsyncMock())
        self.edited_content = []

    async def edit_original_response(self, *, content: str) -> None:
        self.edited_content.append(content)


class MessageHistoryRangeTests(unittest.IsolatedAsyncioTestCase):
    async def test_history_is_inclusive_and_oldest_first(self) -> None:
        channel = FakeChannel(20)
        start = FakeMessage(100, channel=channel, author_id=1)
        middle = FakeMessage(200, channel=channel, author_id=2)
        end = FakeMessage(300, channel=channel, author_id=3)
        channel.history_messages = [middle]

        messages = await bot.get_messages_in_range(start, end)

        self.assertEqual(messages, [start, middle, end])
        self.assertEqual(channel.history_arguments["limit"], 99)
        self.assertEqual(channel.history_arguments["after"].id, start.id)
        self.assertEqual(channel.history_arguments["before"].id, end.id)
        self.assertTrue(channel.history_arguments["oldest_first"])

    async def test_reverse_selection_is_normalized_to_oldest_first(self) -> None:
        channel = FakeChannel(20)
        older = FakeMessage(100, channel=channel, author_id=1)
        middle = FakeMessage(200, channel=channel, author_id=2)
        newer = FakeMessage(300, channel=channel, author_id=3)
        channel.history_messages = [middle]

        messages = await bot.get_messages_in_range(newer, older)

        self.assertEqual(messages, [older, middle, newer])
        self.assertEqual(channel.history_arguments["after"].id, older.id)
        self.assertEqual(channel.history_arguments["before"].id, newer.id)

    async def test_same_start_and_end_returns_one_message(self) -> None:
        channel = FakeChannel(20)
        message = FakeMessage(100, channel=channel, author_id=1)

        messages = await bot.get_messages_in_range(message, message)

        self.assertEqual(messages, [message])
        self.assertIsNone(channel.history_arguments)

    async def test_range_over_limit_is_rejected_instead_of_truncated(
        self,
    ) -> None:
        channel = FakeChannel(20)
        start = FakeMessage(100, channel=channel, author_id=1)
        middle_1 = FakeMessage(200, channel=channel, author_id=2)
        middle_2 = FakeMessage(300, channel=channel, author_id=3)
        end = FakeMessage(400, channel=channel, author_id=4)
        channel.history_messages = [middle_1, middle_2]

        with self.assertRaises(bot.RangeTooLargeError):
            await bot.get_messages_in_range(
                start,
                end,
                max_messages=3,
            )

        self.assertEqual(channel.history_arguments["limit"], 2)


class CompleteMessageRangeTests(unittest.IsolatedAsyncioTestCase):
    async def test_filters_ignored_authors_saves_batch_and_reports_counts(
        self,
    ) -> None:
        channel = FakeChannel(20)
        start = FakeMessage(100, channel=channel, author_id=1)
        ignored = FakeMessage(200, channel=channel, author_id=2)
        end = FakeMessage(300, channel=channel, author_id=3)
        channel.fetched_message = start
        channel.history_messages = [ignored]
        interaction = FakeInteraction()
        pending_range = {
            "guild_id": "10",
            "channel_id": "20",
            "start_message_id": "100",
        }

        with (
            patch.object(
                bot,
                "get_pending_range",
                new=AsyncMock(return_value=pending_range),
            ),
            patch.object(
                bot,
                "get_ignored_user_ids",
                new=AsyncMock(return_value={"2"}),
            ),
            patch.object(
                bot,
                "save_message_range_as_batch",
                new=AsyncMock(
                    return_value=RangeSaveResult(
                        batch_id=7,
                        saved_count=1,
                        already_saved_count=1,
                    )
                ),
            ) as save_range,
        ):
            await bot.complete_message_range(
                interaction=interaction,
                end_message=end,
                expected_start_message_id="100",
                batch_title="  Topic  ",
            )

        call = save_range.await_args
        prepared_messages = call.kwargs["messages"]

        self.assertEqual(call.kwargs["saved_by_user_id"], "42")
        self.assertEqual(call.kwargs["expected_start_message_id"], "100")
        self.assertEqual(call.kwargs["title"], "  Topic  ")
        self.assertEqual(
            [message.message_id for message in prepared_messages],
            ["100", "300"],
        )
        self.assertEqual(
            [message.position for message in prepared_messages],
            [0, 1],
        )
        self.assertIn("Batch: Topic", interaction.edited_content[0])
        self.assertIn("Messages in range: 3", interaction.edited_content[0])
        self.assertIn("Saved: 1", interaction.edited_content[0])
        self.assertIn("Already saved: 1", interaction.edited_content[0])
        self.assertIn("Ignored: 1", interaction.edited_content[0])

    async def test_all_ignored_clears_range_without_creating_batch(self) -> None:
        channel = FakeChannel(20)
        end = FakeMessage(100, channel=channel, author_id=2)
        interaction = FakeInteraction()
        pending_range = {
            "guild_id": "10",
            "channel_id": "20",
            "start_message_id": "100",
        }

        with (
            patch.object(
                bot,
                "get_pending_range",
                new=AsyncMock(return_value=pending_range),
            ),
            patch.object(
                bot,
                "get_ignored_user_ids",
                new=AsyncMock(return_value={"2"}),
            ),
            patch.object(
                bot,
                "delete_pending_range_if_matches",
                new=AsyncMock(return_value=True),
            ) as delete_pending,
            patch.object(
                bot,
                "save_message_range_as_batch",
                new=AsyncMock(),
            ) as save_range,
        ):
            await bot.complete_message_range(
                interaction=interaction,
                end_message=end,
                expected_start_message_id="100",
                batch_title="Ignored range",
            )

        delete_pending.assert_awaited_once_with(
            saved_by_user_id="42",
            expected_start_message_id="100",
        )
        save_range.assert_not_awaited()
        self.assertIn("Saved: 0", interaction.edited_content[0])
        self.assertIn("Ignored: 1", interaction.edited_content[0])
        self.assertIn("No batch was created", interaction.edited_content[0])

    async def test_changed_start_is_not_saved_or_deleted(self) -> None:
        channel = FakeChannel(20)
        end = FakeMessage(300, channel=channel, author_id=3)
        interaction = FakeInteraction()

        with (
            patch.object(
                bot,
                "get_pending_range",
                new=AsyncMock(
                    return_value={
                        "guild_id": "10",
                        "channel_id": "20",
                        "start_message_id": "200",
                    }
                ),
            ),
            patch.object(
                bot,
                "delete_pending_range_if_matches",
                new=AsyncMock(),
            ) as delete_pending,
            patch.object(
                bot,
                "save_message_range_as_batch",
                new=AsyncMock(),
            ) as save_range,
        ):
            await bot.complete_message_range(
                interaction=interaction,
                end_message=end,
                expected_start_message_id="100",
                batch_title="Stale form",
            )

        delete_pending.assert_not_awaited()
        save_range.assert_not_awaited()
        self.assertIn("range start changed", interaction.edited_content[0])

    async def test_different_channel_is_rejected_and_pending_range_is_kept(
        self,
    ) -> None:
        end_channel = FakeChannel(30)
        end = FakeMessage(300, channel=end_channel, author_id=3)
        interaction = FakeInteraction()

        with (
            patch.object(
                bot,
                "get_pending_range",
                new=AsyncMock(
                    return_value={
                        "guild_id": "10",
                        "channel_id": "20",
                        "start_message_id": "100",
                    }
                ),
            ),
            patch.object(
                bot,
                "delete_pending_range_if_matches",
                new=AsyncMock(),
            ) as delete_pending,
            patch.object(
                bot,
                "save_message_range_as_batch",
                new=AsyncMock(),
            ) as save_range,
        ):
            await bot.complete_message_range(
                interaction=interaction,
                end_message=end,
                expected_start_message_id="100",
                batch_title="Wrong channel",
            )

        delete_pending.assert_not_awaited()
        save_range.assert_not_awaited()
        self.assertIn("same channel", interaction.edited_content[0])

    def test_modal_title_is_optional_and_limited(self) -> None:
        channel = FakeChannel(20)
        message = FakeMessage(100, channel=channel, author_id=1)

        modal = bot.SaveRangeModal(
            owner_user_id=42,
            expected_start_message_id="100",
            end_message=message,
        )

        self.assertFalse(modal.batch_title.required)
        self.assertEqual(modal.batch_title.max_length, 100)
        self.assertEqual(modal.timeout, 600)

    async def test_modal_defer_creates_an_ephemeral_response_to_edit(
        self,
    ) -> None:
        channel = FakeChannel(20)
        message = FakeMessage(100, channel=channel, author_id=1)
        interaction = FakeInteraction()
        modal = bot.SaveRangeModal(
            owner_user_id=42,
            expected_start_message_id="100",
            end_message=message,
        )

        with patch.object(
            bot,
            "complete_message_range",
            new=AsyncMock(),
        ) as complete_range:
            await modal.on_submit(interaction)

        interaction.response.defer.assert_awaited_once_with(
            ephemeral=True,
            thinking=True,
        )
        complete_range.assert_awaited_once_with(
            interaction=interaction,
            end_message=message,
            expected_start_message_id="100",
            batch_title="",
        )


if __name__ == "__main__":
    unittest.main()
