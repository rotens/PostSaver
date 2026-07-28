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
        self.guild_id = 10
        self.channel_id = 20
        self.namespace = SimpleNamespace()
        self.response = SimpleNamespace(defer=AsyncMock())
        self.followup = SimpleNamespace(send=AsyncMock())
        self.edit_original_response = AsyncMock()


def saved_row(
    *,
    record_id: int,
    content: str,
    status: str = "UNREAD",
    guild_id: str | None = "10",
    guild_name: str | None = "Test Guild",
    channel_name: str | None = "general",
) -> dict[str, object]:
    return {
        "id": record_id,
        "guild_id": guild_id,
        "guild_name": guild_name,
        "channel_id": "20",
        "channel_name": channel_name,
        "author_name": f"Author {record_id}",
        "content": content,
        "jump_url": f"https://discord.test/{record_id}",
        "message_created_at": "2026-07-23T00:00:00+00:00",
        "status": status,
    }


def attachment_row(
    *,
    saved_message_id: int,
    attachment_id: str,
    filename: str,
    content_type: str | None,
    size: int,
    position: int,
    url: str | None = None,
) -> dict[str, object]:
    return {
        "saved_message_id": saved_message_id,
        "attachment_id": attachment_id,
        "filename": filename,
        "url": url or f"https://cdn.discord.test/{attachment_id}",
        "proxy_url": f"https://proxy.discord.test/{attachment_id}",
        "content_type": content_type,
        "size": size,
        "description": None,
        "width": None,
        "height": None,
        "position": position,
    }


class SavedCommandTests(unittest.IsolatedAsyncioTestCase):
    def test_location_display_uses_ids_for_old_records(self) -> None:
        embed = discord.Embed()

        bot.add_location_to_embed(
            embed,
            guild_id="10",
            guild_name=None,
            channel_id="20",
            channel_name=None,
        )

        self.assertEqual(embed.fields[0].value, "ID: 10")
        self.assertEqual(embed.fields[1].value, "ID: 20")

    def test_location_display_handles_direct_messages(self) -> None:
        embed = discord.Embed()

        bot.add_location_to_embed(
            embed,
            guild_id=None,
            guild_name=None,
            channel_id="20",
            channel_name="Direct Message with Alice",
        )

        self.assertEqual(embed.fields[0].value, "Direct message")
        self.assertEqual(
            embed.fields[1].value,
            "Direct Message with Alice",
        )

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
            filters=bot.SavedMessageFilters(
                status="UNREAD",
                channel_id="20",
                guild_id="10",
            ),
        )
        get_messages.assert_not_awaited()
        interaction.edit_original_response.assert_awaited_once_with(
            content=(
                "No saved messages match these filters.\n"
                "Active filters: Status: `UNREAD` • Channel: <#20> • "
                "Server ID: `10`"
            ),
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
                "The filtered results have 2 page(s).\n"
                "Active filters: Status: `READ_KEEP` • Channel: <#20> • "
                "Server ID: `10`"
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
        attachments_by_message = {
            7: [
                attachment_row(
                    saved_message_id=7,
                    attachment_id="image-1",
                    filename="diagram.png",
                    content_type="image/png",
                    size=2048,
                    position=0,
                ),
                attachment_row(
                    saved_message_id=7,
                    attachment_id="file-1",
                    filename="notes.pdf",
                    content_type="application/pdf",
                    size=4096,
                    position=1,
                ),
            ],
            6: [],
        }

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
            patch.object(
                bot,
                "get_attachments_for_saved_messages",
                new=AsyncMock(return_value=attachments_by_message),
            ) as get_attachments,
        ):
            await bot.show_saved_messages.callback(
                interaction,
                status=SimpleNamespace(value="ALL"),
                page=2,
            )

        get_messages.assert_awaited_once_with(
            saved_by_user_id="42",
            filters=bot.SavedMessageFilters(
                status="ALL",
                channel_id="20",
                guild_id="10",
            ),
            limit=5,
            offset=5,
        )
        get_attachments.assert_awaited_once_with(
            saved_by_user_id="42",
            saved_message_ids=[7, 6],
        )
        interaction.edit_original_response.assert_awaited_once()
        interaction.followup.send.assert_awaited_once()

        first_call = interaction.edit_original_response.await_args
        self.assertEqual(
            first_call.kwargs["content"],
            (
                "Saved messages — page 2/3\n"
                "Active filters: Status: `ALL` • Channel: <#20> • "
                "Server ID: `10`"
            ),
        )
        first_embed = first_call.kwargs["embed"]
        first_view = first_call.kwargs["view"]
        second_call = interaction.followup.send.await_args
        second_embed = second_call.kwargs["embed"]
        second_view = second_call.kwargs["view"]

        self.assertEqual(
            first_embed.description,
            (
                "*This message has no text content; "
                "attachments are listed below.*"
            ),
        )
        self.assertEqual(first_embed.fields[1].name, "Server")
        self.assertEqual(first_embed.fields[1].value, "Test Guild")
        self.assertEqual(first_embed.fields[2].name, "Channel")
        self.assertEqual(first_embed.fields[2].value, "#general")
        self.assertEqual(first_embed.fields[3].name, "Attachments (2)")
        self.assertIn(
            "[diagram.png](https://cdn.discord.test/image-1) — 2 KiB",
            first_embed.fields[3].value,
        )
        self.assertIn(
            "[notes.pdf](https://cdn.discord.test/file-1) — 4 KiB",
            first_embed.fields[3].value,
        )
        self.assertEqual(
            first_embed.image.url,
            "https://proxy.discord.test/image-1",
        )
        self.assertEqual(first_embed.footer.text, "Status: UNREAD | Page 2/3")
        self.assertEqual(len(second_embed.description), 1000)
        self.assertTrue(second_embed.description.endswith("..."))
        self.assertEqual(len(second_embed.fields), 3)
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

    async def test_attachment_list_is_truncated_with_omitted_count(
        self,
    ) -> None:
        interaction = FakeInteraction()
        rows = [saved_row(record_id=7, content="Message")]
        attachments = [
            attachment_row(
                saved_message_id=7,
                attachment_id=f"attachment-{position}",
                filename=f"file-{position}.txt",
                content_type="text/plain",
                size=position + 1,
                position=position,
                url=(
                    "https://cdn.discord.test/"
                    + "x" * 300
                    + str(position)
                ),
            )
            for position in range(10)
        ]

        with (
            patch.object(
                bot,
                "count_saved_messages",
                new=AsyncMock(return_value=1),
            ),
            patch.object(
                bot,
                "get_saved_messages",
                new=AsyncMock(return_value=rows),
            ),
            patch.object(
                bot,
                "get_attachments_for_saved_messages",
                new=AsyncMock(return_value={7: attachments}),
            ),
        ):
            await bot.show_saved_messages.callback(interaction)

        embed = interaction.edit_original_response.await_args.kwargs["embed"]
        attachment_field = embed.fields[3]

        self.assertLessEqual(
            len(attachment_field.value),
            bot.SAVED_ATTACHMENT_FIELD_VALUE_LIMIT,
        )
        self.assertIn("more attachments omitted", attachment_field.value)
        self.assertIsNone(embed.image.url)


if __name__ == "__main__":
    unittest.main()
