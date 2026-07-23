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
        self.followup = SimpleNamespace(send=AsyncMock())
        self.edit_original_response = AsyncMock()


def saved_row(
    *,
    record_id: int,
    content: str,
    status: str = "UNREAD",
) -> dict[str, object]:
    return {
        "id": record_id,
        "author_name": f"Author {record_id}",
        "content": content,
        "jump_url": f"https://discord.test/{record_id}",
        "message_created_at": "2026-07-23T00:00:00+00:00",
        "status": status,
    }


class SavedCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_filter_reports_no_unread_messages(self) -> None:
        interaction = FakeInteraction()

        with (
            patch.object(
                bot,
                "count_saved_messages",
                new=AsyncMock(return_value=0),
            ) as count_messages,
            patch.object(
                bot,
                "get_saved_messages",
                new=AsyncMock(),
            ) as get_messages,
        ):
            await bot.show_saved_messages.callback(interaction)

        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        count_messages.assert_awaited_once_with(
            saved_by_user_id="42",
            status="UNREAD",
        )
        get_messages.assert_not_awaited()
        interaction.edit_original_response.assert_awaited_once_with(
            content="You have no saved messages with status `UNREAD`.",
        )

    async def test_page_above_filtered_total_is_rejected(self) -> None:
        interaction = FakeInteraction()
        status = SimpleNamespace(value="READ_KEEP")

        with (
            patch.object(
                bot,
                "count_saved_messages",
                new=AsyncMock(return_value=6),
            ),
            patch.object(
                bot,
                "get_saved_messages",
                new=AsyncMock(),
            ) as get_messages,
        ):
            await bot.show_saved_messages.callback(
                interaction,
                status=status,
                page=3,
            )

        get_messages.assert_not_awaited()
        interaction.edit_original_response.assert_awaited_once_with(
            content=(
                "Page `3` does not exist. "
                "You have 2 page(s) with status `READ_KEEP`."
            ),
        )

    async def test_valid_page_uses_filtered_offset_and_one_panel_per_row(
        self,
    ) -> None:
        interaction = FakeInteraction()
        rows = [
            saved_row(record_id=7, content="   "),
            saved_row(
                record_id=6,
                content="x" * 1001,
                status="READ_KEEP",
            ),
        ]

        with (
            patch.object(
                bot,
                "count_saved_messages",
                new=AsyncMock(return_value=12),
            ),
            patch.object(
                bot,
                "get_saved_messages",
                new=AsyncMock(return_value=rows),
            ) as get_messages,
        ):
            await bot.show_saved_messages.callback(
                interaction,
                status=SimpleNamespace(value="ALL"),
                page=2,
            )

        get_messages.assert_awaited_once_with(
            saved_by_user_id="42",
            status="ALL",
            limit=5,
            offset=5,
        )
        interaction.edit_original_response.assert_awaited_once()
        interaction.followup.send.assert_awaited_once()

        first_call = interaction.edit_original_response.await_args
        first_embed = first_call.kwargs["embed"]
        first_view = first_call.kwargs["view"]
        second_call = interaction.followup.send.await_args
        second_embed = second_call.kwargs["embed"]
        second_view = second_call.kwargs["view"]

        self.assertEqual(
            first_embed.description,
            "*Message has no text content.*",
        )
        self.assertEqual(first_embed.footer.text, "Status: UNREAD | Page 2/3")
        self.assertEqual(len(second_embed.description), 1000)
        self.assertTrue(second_embed.description.endswith("..."))
        self.assertEqual(
            second_embed.footer.text,
            "Status: READ_KEEP | Page 2/3",
        )
        self.assertEqual(first_view.record_id, 7)
        self.assertEqual(second_view.record_id, 6)
        self.assertEqual(first_view.owner_user_id, 42)
        self.assertTrue(
            next(
                item
                for item in first_view.children
                if item.custom_id == "saved:unread"
            ).disabled
        )
        self.assertTrue(second_call.kwargs["ephemeral"])


if __name__ == "__main__":
    unittest.main()
