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
        self.response = SimpleNamespace(
            defer=AsyncMock(),
            edit_message=AsyncMock(),
            send_message=AsyncMock(),
        )
        self.edit_original_response = AsyncMock()


def get_button(
    view: discord.ui.View,
    custom_id: str,
) -> discord.ui.Button:
    return next(
        item
        for item in view.children
        if isinstance(item, discord.ui.Button)
        and item.custom_id == custom_id
    )


def saved_message_row(
    *,
    record_id: int,
    content: str = "Saved message content",
    status: str = "UNREAD",
) -> dict[str, object]:
    return {
        "id": record_id,
        "guild_id": "10",
        "guild_name": "Test Guild",
        "channel_id": "20",
        "channel_name": "general",
        "author_name": f"Author {record_id}",
        "content": content,
        "jump_url": f"https://discord.test/messages/{record_id}",
        "message_created_at": "2026-07-26T12:00:00+00:00",
        "status": status,
        "position": record_id,
    }


def attachment_row(
    *,
    saved_message_id: int,
    attachment_id: str,
    filename: str,
    content_type: str | None,
    position: int = 0,
    url_suffix: str = "",
) -> dict[str, object]:
    return {
        "saved_message_id": saved_message_id,
        "attachment_id": attachment_id,
        "filename": filename,
        "url": f"https://cdn.discord.test/{attachment_id}{url_suffix}",
        "proxy_url": f"https://proxy.discord.test/{attachment_id}",
        "content_type": content_type,
        "size": 2048,
        "description": None,
        "width": 800 if content_type == "image/png" else None,
        "height": 600 if content_type == "image/png" else None,
        "position": position,
    }


class BatchSummaryViewTests(unittest.IsolatedAsyncioTestCase):
    def create_view(
        self,
        *,
        message_count: int = 3,
    ) -> bot.BatchSummaryView:
        return bot.BatchSummaryView(
            batch_id=17,
            owner_user_id=42,
            title="Architecture",
            message_count=message_count,
        )

    def test_view_button_targets_batch_and_empty_batch_disables_it(
        self,
    ) -> None:
        populated_view = self.create_view()
        empty_view = self.create_view(message_count=0)

        self.assertEqual(populated_view.batch_id, 17)
        self.assertFalse(populated_view.view_batch.disabled)
        self.assertTrue(empty_view.view_batch.disabled)
        self.assertEqual(populated_view.timeout, 600)

    async def test_interaction_check_is_owner_scoped(self) -> None:
        view = self.create_view()
        owner_interaction = FakeInteraction(user_id=42)
        other_interaction = FakeInteraction(user_id=99)

        self.assertTrue(await view.interaction_check(owner_interaction))
        self.assertFalse(await view.interaction_check(other_interaction))
        other_interaction.response.send_message.assert_awaited_once_with(
            "This batch-summary panel belongs to another user.",
            ephemeral=True,
        )

    async def test_view_button_opens_its_own_batch(self) -> None:
        view = self.create_view()
        interaction = FakeInteraction()

        with patch.object(
            bot,
            "open_saved_batch_detail",
            new=AsyncMock(),
        ) as open_detail:
            await view.view_batch.callback(interaction)

        open_detail.assert_awaited_once_with(
            interaction,
            batch_id=17,
            title="Architecture",
        )


class BatchDetailPageTests(unittest.IsolatedAsyncioTestCase):
    async def test_page_below_one_is_rejected_before_database_query(
        self,
    ) -> None:
        with patch.object(
            bot,
            "count_saved_messages_in_batch",
            new=AsyncMock(),
        ) as count_messages:
            with self.assertRaisesRegex(
                ValueError,
                "Page number must be at least 1",
            ):
                await bot.get_saved_batch_detail_page(
                    batch_id=17,
                    saved_by_user_id="42",
                    requested_page=0,
                )

        count_messages.assert_not_awaited()

    async def test_page_fetches_owned_messages_and_renders_attachments(
        self,
    ) -> None:
        rows = [
            saved_message_row(record_id=6, content="   "),
            saved_message_row(
                record_id=7,
                content="x" * (bot.BATCH_DETAIL_CONTENT_LIMIT + 20),
                status="READ_KEEP",
            ),
        ]
        attachments = {
            6: [
                attachment_row(
                    saved_message_id=6,
                    attachment_id="image-1",
                    filename="diagram.png",
                    content_type="image/png",
                )
            ],
            7: [
                attachment_row(
                    saved_message_id=7,
                    attachment_id="file-1",
                    filename="notes.pdf",
                    content_type="application/pdf",
                )
            ],
        }

        with (
            patch.object(
                bot,
                "count_saved_messages_in_batch",
                new=AsyncMock(return_value=7),
            ) as count_messages,
            patch.object(
                bot,
                "get_saved_messages_in_batch",
                new=AsyncMock(return_value=rows),
            ) as get_messages,
            patch.object(
                bot,
                "get_attachments_for_saved_messages",
                new=AsyncMock(return_value=attachments),
            ) as get_attachments,
        ):
            page = await bot.get_saved_batch_detail_page(
                batch_id=17,
                saved_by_user_id="42",
                requested_page=2,
            )

        self.assertIsNotNone(page)
        assert page is not None
        count_messages.assert_awaited_once_with(
            batch_id=17,
            saved_by_user_id="42",
        )
        get_messages.assert_awaited_once_with(
            batch_id=17,
            saved_by_user_id="42",
            limit=5,
            offset=5,
        )
        get_attachments.assert_awaited_once_with(
            saved_by_user_id="42",
            saved_message_ids=[6, 7],
        )
        self.assertEqual(page.current_page, 2)
        self.assertEqual(page.total_pages, 2)
        self.assertEqual(page.total_messages, 7)
        self.assertEqual(len(page.embeds), 2)

        first_embed, second_embed = page.embeds
        self.assertIn(
            "This message has no text content",
            first_embed.description,
        )
        self.assertIn("Open message", first_embed.description)
        self.assertEqual(first_embed.fields[1].name, "Server")
        self.assertEqual(first_embed.fields[1].value, "Test Guild")
        self.assertEqual(first_embed.fields[2].name, "Channel")
        self.assertEqual(first_embed.fields[2].value, "#general")
        self.assertEqual(first_embed.fields[3].name, "Attachments (1)")
        self.assertIn("diagram.png", first_embed.fields[3].value)
        self.assertEqual(
            first_embed.image.url,
            "https://proxy.discord.test/image-1",
        )
        self.assertEqual(
            first_embed.footer.text,
            "Status: UNREAD | Message 6/7 | Page 2/2",
        )
        self.assertIn(
            "x" * (bot.BATCH_DETAIL_CONTENT_LIMIT - 3) + "...",
            second_embed.description,
        )
        self.assertIn("notes.pdf", second_embed.fields[3].value)
        self.assertIsNone(second_embed.image.url)
        self.assertEqual(
            second_embed.footer.text,
            "Status: READ_KEEP | Message 7/7 | Page 2/2",
        )

    async def test_empty_batch_stops_before_message_queries(self) -> None:
        with (
            patch.object(
                bot,
                "count_saved_messages_in_batch",
                new=AsyncMock(return_value=0),
            ),
            patch.object(
                bot,
                "get_saved_messages_in_batch",
                new=AsyncMock(),
            ) as get_messages,
            patch.object(
                bot,
                "get_attachments_for_saved_messages",
                new=AsyncMock(),
            ) as get_attachments,
        ):
            page = await bot.get_saved_batch_detail_page(
                batch_id=17,
                saved_by_user_id="42",
                requested_page=1,
            )

        self.assertIsNone(page)
        get_messages.assert_not_awaited()
        get_attachments.assert_not_awaited()

    async def test_requested_page_is_clamped_to_current_last_page(
        self,
    ) -> None:
        rows = [saved_message_row(record_id=11)]

        with (
            patch.object(
                bot,
                "count_saved_messages_in_batch",
                new=AsyncMock(return_value=11),
            ),
            patch.object(
                bot,
                "get_saved_messages_in_batch",
                new=AsyncMock(return_value=rows),
            ) as get_messages,
            patch.object(
                bot,
                "get_attachments_for_saved_messages",
                new=AsyncMock(return_value={11: []}),
            ),
        ):
            page = await bot.get_saved_batch_detail_page(
                batch_id=17,
                saved_by_user_id="42",
                requested_page=99,
            )

        self.assertIsNotNone(page)
        assert page is not None
        self.assertEqual(page.current_page, 3)
        get_messages.assert_awaited_once_with(
            batch_id=17,
            saved_by_user_id="42",
            limit=5,
            offset=10,
        )
        self.assertIn("Message 11/11", page.embeds[0].footer.text)

    async def test_full_detail_page_stays_within_embed_text_limit(
        self,
    ) -> None:
        rows = [
            saved_message_row(record_id=record_id, content="x" * 2000)
            for record_id in range(1, 6)
        ]
        attachments = {
            record_id: [
                attachment_row(
                    saved_message_id=record_id,
                    attachment_id=f"{record_id}-{position}",
                    filename=f"attachment-{position}.png",
                    content_type="image/png",
                    position=position,
                    url_suffix="x" * 300,
                )
                for position in range(5)
            ]
            for record_id in range(1, 6)
        }

        with (
            patch.object(
                bot,
                "count_saved_messages_in_batch",
                new=AsyncMock(return_value=5),
            ),
            patch.object(
                bot,
                "get_saved_messages_in_batch",
                new=AsyncMock(return_value=rows),
            ),
            patch.object(
                bot,
                "get_attachments_for_saved_messages",
                new=AsyncMock(return_value=attachments),
            ),
        ):
            page = await bot.get_saved_batch_detail_page(
                batch_id=17,
                saved_by_user_id="42",
                requested_page=1,
            )

        self.assertIsNotNone(page)
        assert page is not None
        self.assertEqual(len(page.embeds), 5)
        self.assertLessEqual(sum(len(embed) for embed in page.embeds), 6000)
        self.assertTrue(
            all(
                len(embed.fields[3].value)
                <= bot.BATCH_ATTACHMENT_FIELD_VALUE_LIMIT
                for embed in page.embeds
            )
        )


class BatchDetailViewTests(unittest.IsolatedAsyncioTestCase):
    def create_view(
        self,
        *,
        current_page: int = 1,
        total_pages: int = 3,
    ) -> bot.BatchDetailView:
        return bot.BatchDetailView(
            batch_id=17,
            owner_user_id=42,
            title="Design *notes*",
            current_page=current_page,
            total_pages=total_pages,
        )

    def test_boundary_buttons_match_current_page(self) -> None:
        first_page = self.create_view(current_page=1)
        middle_page = self.create_view(current_page=2)
        last_page = self.create_view(current_page=3)

        self.assertTrue(first_page.previous_page.disabled)
        self.assertFalse(first_page.next_page.disabled)
        self.assertFalse(middle_page.previous_page.disabled)
        self.assertFalse(middle_page.next_page.disabled)
        self.assertFalse(last_page.previous_page.disabled)
        self.assertTrue(last_page.next_page.disabled)
        self.assertEqual(first_page.timeout, 600)

    async def test_interaction_check_is_owner_scoped(self) -> None:
        view = self.create_view()
        other_interaction = FakeInteraction(user_id=99)

        self.assertTrue(
            await view.interaction_check(FakeInteraction(user_id=42))
        )
        self.assertFalse(await view.interaction_check(other_interaction))
        other_interaction.response.send_message.assert_awaited_once_with(
            "This batch-detail view belongs to another user.",
            ephemeral=True,
        )

    async def test_next_button_loads_and_edits_the_next_page(self) -> None:
        view = self.create_view(current_page=1)
        interaction = FakeInteraction()
        page = bot.SavedBatchDetailPage(
            current_page=2,
            total_pages=3,
            total_messages=12,
            embeds=(discord.Embed(title="Message 6"),),
        )

        with patch.object(
            bot,
            "get_saved_batch_detail_page",
            new=AsyncMock(return_value=page),
        ) as get_page:
            await get_button(
                view,
                "batch_detail:next",
            ).callback(interaction)

        get_page.assert_awaited_once_with(
            batch_id=17,
            saved_by_user_id="42",
            requested_page=2,
        )
        self.assertEqual(view.current_page, 2)
        self.assertFalse(view.previous_page.disabled)
        self.assertFalse(view.next_page.disabled)
        interaction.response.defer.assert_awaited_once_with()
        edit_call = interaction.edit_original_response.await_args
        self.assertEqual(
            edit_call.kwargs["content"],
            "Batch: **Design \\*notes\\*** — page 2/3",
        )
        self.assertEqual(edit_call.kwargs["embeds"], list(page.embeds))
        self.assertIs(edit_call.kwargs["view"], view)

    async def test_empty_batch_removes_stale_detail_view(self) -> None:
        view = self.create_view()
        interaction = FakeInteraction()

        with patch.object(
            bot,
            "get_saved_batch_detail_page",
            new=AsyncMock(return_value=None),
        ):
            await view.show_page(interaction, 2)

        self.assertTrue(view.is_finished())
        interaction.response.defer.assert_awaited_once_with()
        interaction.edit_original_response.assert_awaited_once_with(
            content="This batch no longer contains any saved messages.",
            embeds=[],
            view=None,
        )

    async def test_open_detail_creates_separate_ephemeral_response(
        self,
    ) -> None:
        interaction = FakeInteraction()
        page = bot.SavedBatchDetailPage(
            current_page=1,
            total_pages=2,
            total_messages=6,
            embeds=(discord.Embed(title="Message 1"),),
        )

        with patch.object(
            bot,
            "get_saved_batch_detail_page",
            new=AsyncMock(return_value=page),
        ) as get_page:
            await bot.open_saved_batch_detail(
                interaction,
                batch_id=17,
                title="Architecture",
            )

        interaction.response.defer.assert_awaited_once_with(
            ephemeral=True,
            thinking=True,
        )
        get_page.assert_awaited_once_with(
            batch_id=17,
            saved_by_user_id="42",
            requested_page=1,
        )
        edit_call = interaction.edit_original_response.await_args
        self.assertEqual(
            edit_call.kwargs["content"],
            "Batch: **Architecture** — page 1/2",
        )
        self.assertEqual(edit_call.kwargs["embeds"], list(page.embeds))
        detail_view = edit_call.kwargs["view"]
        self.assertIsInstance(detail_view, bot.BatchDetailView)
        self.assertEqual(detail_view.batch_id, 17)
        self.assertEqual(detail_view.owner_user_id, 42)

    async def test_open_detail_reports_batch_that_became_empty(self) -> None:
        interaction = FakeInteraction()

        with patch.object(
            bot,
            "get_saved_batch_detail_page",
            new=AsyncMock(return_value=None),
        ):
            await bot.open_saved_batch_detail(
                interaction,
                batch_id=17,
                title="Architecture",
            )

        interaction.edit_original_response.assert_awaited_once_with(
            content="This batch no longer contains any saved messages.",
        )


if __name__ == "__main__":
    unittest.main()
