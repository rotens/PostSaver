import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord


os.environ.setdefault("DISCORD_TOKEN", "test-token")

with patch.object(discord.Client, "run"):
    import bot


def get_button(
    view: bot.SavedMessageView,
    custom_id: str,
) -> discord.ui.Button:
    return next(
        item
        for item in view.children
        if isinstance(item, discord.ui.Button)
        and item.custom_id == custom_id
    )


class FakeInteraction:
    def __init__(
        self,
        *,
        user_id: int = 42,
        embed: discord.Embed | None = None,
    ) -> None:
        self.user = SimpleNamespace(id=user_id)
        self.response = SimpleNamespace(
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
        )
        self.message = SimpleNamespace(
            embeds=[embed or discord.Embed(title="Saved message")]
        )


class SavedMessageViewTests(unittest.IsolatedAsyncioTestCase):
    def create_view(
        self,
        *,
        current_status: str = "UNREAD",
    ) -> bot.SavedMessageView:
        return bot.SavedMessageView(
            record_id=7,
            owner_user_id=42,
            jump_url="https://discord.test/100",
            current_status=current_status,
            page_number=2,
            total_pages=3,
        )

    def test_current_status_button_is_disabled_and_open_url_is_present(
        self,
    ) -> None:
        unread_view = self.create_view(current_status="UNREAD")
        read_keep_view = self.create_view(current_status="READ_KEEP")

        unread_button = get_button(unread_view, "saved:unread")
        unread_read_keep_button = get_button(
            unread_view,
            "saved:read_keep",
        )
        read_keep_button = get_button(
            read_keep_view,
            "saved:read_keep",
        )
        open_button = next(
            item
            for item in unread_view.children
            if isinstance(item, discord.ui.Button)
            and item.style is discord.ButtonStyle.link
        )

        self.assertTrue(unread_button.disabled)
        self.assertFalse(unread_read_keep_button.disabled)
        self.assertTrue(read_keep_button.disabled)
        self.assertEqual(open_button.url, "https://discord.test/100")
        self.assertEqual(unread_view.timeout, 600)

    async def test_interaction_check_allows_owner(self) -> None:
        view = self.create_view()
        interaction = FakeInteraction(user_id=42)

        is_allowed = await view.interaction_check(interaction)

        self.assertTrue(is_allowed)
        interaction.response.send_message.assert_not_awaited()

    async def test_interaction_check_rejects_other_user_ephemerally(
        self,
    ) -> None:
        view = self.create_view()
        interaction = FakeInteraction(user_id=99)

        is_allowed = await view.interaction_check(interaction)

        self.assertFalse(is_allowed)
        interaction.response.send_message.assert_awaited_once_with(
            "This saved-message panel belongs to another user.",
            ephemeral=True,
        )

    async def test_status_change_updates_owned_record_footer_and_buttons(
        self,
    ) -> None:
        view = self.create_view(current_status="UNREAD")
        embed = discord.Embed(title="Saved message")
        embed.set_footer(text="Status: UNREAD | Page 2/3")
        interaction = FakeInteraction(embed=embed)

        with patch.object(
            bot,
            "update_saved_message_status",
            new=AsyncMock(return_value=True),
        ) as update_status:
            await view.set_status(interaction, "READ_KEEP")

        update_status.assert_awaited_once_with(
            record_id=7,
            saved_by_user_id="42",
            status="READ_KEEP",
        )
        self.assertEqual(view.current_status, "READ_KEEP")
        self.assertTrue(
            get_button(view, "saved:read_keep").disabled
        )
        self.assertFalse(get_button(view, "saved:unread").disabled)
        self.assertEqual(
            interaction.message.embeds[0].footer.text,
            "Status: READ_KEEP | Page 2/3",
        )
        interaction.response.edit_message.assert_awaited_once_with(
            embed=interaction.message.embeds[0],
            view=view,
        )

    async def test_status_buttons_request_their_matching_status(self) -> None:
        view = self.create_view()
        interaction = FakeInteraction()

        with patch.object(
            view,
            "set_status",
            new=AsyncMock(),
        ) as set_status:
            await get_button(
                view,
                "saved:read_keep",
            ).callback(interaction)
            await get_button(
                view,
                "saved:unread",
            ).callback(interaction)

        self.assertEqual(
            set_status.await_args_list,
            [
                unittest.mock.call(interaction, "READ_KEEP"),
                unittest.mock.call(interaction, "UNREAD"),
            ],
        )

    async def test_missing_record_removes_stale_panel(self) -> None:
        view = self.create_view()
        interaction = FakeInteraction()

        with patch.object(
            bot,
            "update_saved_message_status",
            new=AsyncMock(return_value=False),
        ):
            await view.set_status(interaction, "READ_KEEP")

        self.assertTrue(view.is_finished())
        interaction.response.edit_message.assert_awaited_once_with(
            content="This record no longer exists in the database.",
            embed=None,
            view=None,
        )

    async def test_delete_button_deletes_owned_record_and_removes_panel(
        self,
    ) -> None:
        view = self.create_view()
        interaction = FakeInteraction()
        delete_button = get_button(view, "saved:delete")

        with patch.object(
            bot,
            "delete_saved_message",
            new=AsyncMock(return_value=True),
        ) as delete_message:
            await delete_button.callback(interaction)

        delete_message.assert_awaited_once_with(
            record_id=7,
            saved_by_user_id="42",
        )
        self.assertTrue(view.is_finished())
        interaction.response.edit_message.assert_awaited_once_with(
            content="The saved message was deleted from the database.",
            embed=None,
            view=None,
        )

    async def test_delete_button_reports_already_deleted_record(self) -> None:
        view = self.create_view()
        interaction = FakeInteraction()
        delete_button = get_button(view, "saved:delete")

        with patch.object(
            bot,
            "delete_saved_message",
            new=AsyncMock(return_value=False),
        ):
            await delete_button.callback(interaction)

        interaction.response.edit_message.assert_awaited_once_with(
            content="This record was already deleted.",
            embed=None,
            view=None,
        )


if __name__ == "__main__":
    unittest.main()
