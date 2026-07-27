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
        self.followup = SimpleNamespace(send=AsyncMock())


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


def attachment_row(
    *,
    saved_message_id: int,
    attachment_id: str,
    filename: str,
    content_type: str | None,
    size: int,
    position: int,
) -> dict[str, object]:
    return {
        "saved_message_id": saved_message_id,
        "attachment_id": attachment_id,
        "filename": filename,
        "url": f"https://cdn.discord.test/{attachment_id}",
        "proxy_url": f"https://proxy.discord.test/{attachment_id}",
        "content_type": content_type,
        "size": size,
        "description": None,
        "width": None,
        "height": None,
        "position": position,
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
        attachments_by_message = {
            70: [
                attachment_row(
                    saved_message_id=70,
                    attachment_id="image-1",
                    filename="architecture.png",
                    content_type="image/png",
                    size=2048,
                    position=0,
                ),
                attachment_row(
                    saved_message_id=70,
                    attachment_id="file-1",
                    filename="architecture.pdf",
                    content_type="application/pdf",
                    size=4096,
                    position=1,
                ),
            ],
            60: [
                attachment_row(
                    saved_message_id=60,
                    attachment_id="file-2",
                    filename="notes.txt",
                    content_type="text/plain",
                    size=512,
                    position=0,
                ),
            ],
        }

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
            patch.object(
                bot,
                "get_attachments_for_saved_messages",
                new=AsyncMock(return_value=attachments_by_message),
            ) as get_attachments,
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
        get_attachments.assert_awaited_once_with(
            saved_by_user_id="42",
            saved_message_ids=[70, 60],
        )
        interaction.edit_original_response.assert_awaited_once()
        first_call = interaction.edit_original_response.await_args
        followup_calls = interaction.followup.send.await_args_list
        summary_calls = [first_call, *followup_calls]
        embeds = [call.kwargs["embed"] for call in summary_calls]
        views = [call.kwargs["view"] for call in summary_calls]

        self.assertEqual(
            first_call.kwargs["content"],
            "Your saved message batches — page 2/3",
        )
        self.assertEqual(len(followup_calls), 2)
        self.assertTrue(
            all(call.kwargs["ephemeral"] for call in followup_calls)
        )
        self.assertEqual(len(embeds), 3)
        self.assertTrue(
            all(isinstance(view, bot.BatchSummaryView) for view in views)
        )
        self.assertEqual([view.batch_id for view in views], [7, 6, 5])
        self.assertEqual(
            [view.owner_user_id for view in views],
            [42, 42, 42],
        )
        self.assertFalse(views[0].view_batch.disabled)
        self.assertFalse(views[1].view_batch.disabled)
        self.assertTrue(views[2].view_batch.disabled)
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
        self.assertEqual(embeds[0].fields[2].name, "Attachments (2)")
        self.assertIn("architecture.png", embeds[0].fields[2].value)
        self.assertIn("architecture.pdf", embeds[0].fields[2].value)
        self.assertEqual(
            embeds[0].image.url,
            "https://proxy.discord.test/image-1",
        )
        self.assertEqual(embeds[0].footer.text, "Page 2/3")

        self.assertEqual(embeds[1].title, "Untitled batch #6")
        self.assertIn(
            (
                "*First message has no text content; "
                "attachments are listed below.*"
            ),
            embeds[1].description,
        )
        self.assertEqual(embeds[1].fields[2].name, "Attachments (1)")
        self.assertIn("notes.txt", embeds[1].fields[2].value)
        self.assertIsNone(embeds[1].image.url)
        self.assertEqual(embeds[2].title, "Empty batch")
        self.assertEqual(
            embeds[2].description,
            "*This batch currently has no messages.*",
        )
        self.assertEqual(len(embeds[2].fields), 2)

    async def test_full_page_stays_within_discord_embed_text_limit(
        self,
    ) -> None:
        interaction = FakeInteraction()
        rows = [
            batch_summary(
                batch_id=batch_id,
                title="T" * 100,
                message_count=10,
                content="x" * 1000,
            )
            for batch_id in range(1, 6)
        ]
        attachments_by_message = {
            batch_id * 10: [
                attachment_row(
                    saved_message_id=batch_id * 10,
                    attachment_id=f"{batch_id}-{position}",
                    filename=f"attachment-{position}.png",
                    content_type="image/png",
                    size=2048,
                    position=position,
                )
                for position in range(5)
            ]
            for batch_id in range(1, 6)
        }

        with (
            patch.object(
                bot,
                "count_saved_batches",
                new=AsyncMock(return_value=5),
            ),
            patch.object(
                bot,
                "get_saved_batches",
                new=AsyncMock(return_value=rows),
            ),
            patch.object(
                bot,
                "get_attachments_for_saved_messages",
                new=AsyncMock(return_value=attachments_by_message),
            ),
        ):
            await bot.show_saved_batches.callback(interaction)

        summary_calls = [
            interaction.edit_original_response.await_args,
            *interaction.followup.send.await_args_list,
        ]
        embeds = [call.kwargs["embed"] for call in summary_calls]

        self.assertEqual(len(embeds), 5)
        self.assertTrue(all(len(embed) <= 6000 for embed in embeds))
        self.assertTrue(
            all(
                len(embed.fields[2].value)
                <= bot.BATCH_ATTACHMENT_FIELD_VALUE_LIMIT
                for embed in embeds
            )
        )


if __name__ == "__main__":
    unittest.main()
