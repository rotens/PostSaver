import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord


os.environ.setdefault("DISCORD_TOKEN", "test-token")

with patch.object(discord.Client, "run"):
    import bot


class FakeInteraction:
    def __init__(self, user_id: int = 42) -> None:
        self.user = SimpleNamespace(id=user_id)
        self.response = SimpleNamespace(defer=AsyncMock())
        self.edit_original_response = AsyncMock()


def batch_summary(
    *,
    batch_id: int,
    title: str | None,
    message_count: int,
    content: str | None = "First message content",
) -> dict[str, object]:
    has_messages = message_count > 0

    return {
        "id": batch_id,
        "title": title,
        "created_at": "2026-07-24 12:00:00",
        "message_count": message_count,
        "first_message_record_id": batch_id * 10 if has_messages else None,
        "first_message_author_name": (
            f"Author {batch_id}" if has_messages else None
        ),
        "first_message_content": content if has_messages else None,
        "first_message_jump_url": (
            f"https://discord.test/{batch_id}" if has_messages else None
        ),
        "first_message_created_at": (
            "2026-07-24T11:00:00+00:00" if has_messages else None
        ),
        "first_message_status": "UNREAD" if has_messages else None,
    }


class BatchesCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_batches_returns_ephemeral_empty_state(self) -> None:
        interaction = FakeInteraction()

        with (
            patch.object(
                bot,
                "count_saved_batches",
                new=AsyncMock(return_value=0),
            ) as count_batches,
            patch.object(
                bot,
                "get_saved_batches",
                new=AsyncMock(),
            ) as get_batches,
        ):
            await bot.show_saved_batches.callback(interaction)

        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        count_batches.assert_awaited_once_with(
            saved_by_user_id="42",
        )
        get_batches.assert_not_awaited()
        interaction.edit_original_response.assert_awaited_once_with(
            content="You have no saved message batches.",
        )

    async def test_page_above_total_is_rejected_before_querying_rows(
        self,
    ) -> None:
        interaction = FakeInteraction()

        with (
            patch.object(
                bot,
                "count_saved_batches",
                new=AsyncMock(return_value=6),
            ),
            patch.object(
                bot,
                "get_saved_batches",
                new=AsyncMock(),
            ) as get_batches,
        ):
            await bot.show_saved_batches.callback(
                interaction,
                page=3,
            )

        get_batches.assert_not_awaited()
        interaction.edit_original_response.assert_awaited_once_with(
            content=(
                "Page `3` does not exist. "
                "You have 2 batch page(s)."
            ),
        )

    async def test_valid_page_renders_paginated_read_only_summaries(
        self,
    ) -> None:
        interaction = FakeInteraction()
        long_content = "x" * (bot.BATCH_PREVIEW_CONTENT_LIMIT + 1)
        rows = [
            batch_summary(
                batch_id=7,
                title="Architecture",
                message_count=12,
                content=long_content,
            ),
            batch_summary(
                batch_id=6,
                title=None,
                message_count=1,
                content="   ",
            ),
            batch_summary(
                batch_id=5,
                title="Empty batch",
                message_count=0,
            ),
        ]

        with (
            patch.object(
                bot,
                "count_saved_batches",
                new=AsyncMock(return_value=12),
            ),
            patch.object(
                bot,
                "get_saved_batches",
                new=AsyncMock(return_value=rows),
            ) as get_batches,
        ):
            await bot.show_saved_batches.callback(
                interaction,
                page=2,
            )

        get_batches.assert_awaited_once_with(
            saved_by_user_id="42",
            limit=5,
            offset=5,
        )
        interaction.edit_original_response.assert_awaited_once()
        call = interaction.edit_original_response.await_args
        embeds = call.kwargs["embeds"]

        self.assertEqual(
            call.kwargs["content"],
            "Your saved message batches — page 2/3",
        )
        self.assertEqual(len(embeds), 3)
        self.assertEqual(embeds[0].title, "Architecture")
        self.assertIn("First message by Author 7", embeds[0].description)
        self.assertIn(
            "x" * (bot.BATCH_PREVIEW_CONTENT_LIMIT - 3) + "...",
            embeds[0].description,
        )
        self.assertIn(
            "[Open first message](https://discord.test/7)",
            embeds[0].description,
        )
        self.assertEqual(embeds[0].fields[0].name, "Created")
        self.assertEqual(embeds[0].fields[0].value, "2026-07-24 12:00:00")
        self.assertEqual(embeds[0].fields[1].name, "Messages")
        self.assertEqual(embeds[0].fields[1].value, "12")
        self.assertEqual(embeds[0].footer.text, "Page 2/3")

        self.assertEqual(embeds[1].title, "Untitled batch #6")
        self.assertIn(
            "*First message has no text content.*",
            embeds[1].description,
        )
        self.assertEqual(embeds[2].title, "Empty batch")
        self.assertEqual(
            embeds[2].description,
            "*This batch currently has no messages.*",
        )
        self.assertNotIn("view", call.kwargs)


if __name__ == "__main__":
    unittest.main()
