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
    def __init__(self, user_id: int = 30) -> None:
        self.id = user_id

    def __str__(self) -> str:
        return f"Author {self.id}"


class FakeAttachment:
    def __init__(self, attachment_id: int = 501) -> None:
        self.id = attachment_id
        self.filename = "diagram.png"
        self.url = "https://cdn.discord.test/diagram.png"
        self.proxy_url = "https://proxy.discord.test/diagram.png"
        self.content_type = "image/png"
        self.size = 2048
        self.description = "Diagram"
        self.width = 1280
        self.height = 720


class FakeMessage:
    def __init__(self) -> None:
        self.id = 100
        self.guild = SimpleNamespace(id=10, name="Test Guild")
        self.channel = SimpleNamespace(id=20, name="general")
        self.author = FakeAuthor()
        self.content = "Message content"
        self.jump_url = "https://discord.test/100"
        self.created_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
        self.attachments = [FakeAttachment()]


class FakeInteraction:
    def __init__(self, user_id: int = 42) -> None:
        self.user = SimpleNamespace(id=user_id)
        self.response = SimpleNamespace(
            send_message=AsyncMock(),
            send_modal=AsyncMock(),
            edit_message=AsyncMock(),
        )


def recent_batch(
    batch_id: int,
    *,
    title: str | None,
    message_count: int,
) -> dict:
    return {
        "id": batch_id,
        "title": title,
        "created_at": "2026-08-01 12:00:00",
        "message_count": message_count,
    }


class CreateBatchCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_batch_commands_are_registered(self) -> None:
        commands = bot.bot.tree.get_commands()
        command_names = {command.name for command in commands}

        self.assertIn("create_batch", command_names)
        self.assertIn("Add to batch", command_names)

    async def test_create_batch_normalizes_title_and_confirms(self) -> None:
        interaction = FakeInteraction()

        with patch.object(
            bot,
            "create_saved_batch",
            new=AsyncMock(return_value=17),
        ) as create_batch:
            await bot.create_batch_command.callback(
                interaction,
                title="  Design *notes*  ",
            )

        create_batch.assert_awaited_once_with(
            saved_by_user_id="42",
            title="Design *notes*",
        )
        interaction.response.send_message.assert_awaited_once_with(
            (
                "Created **Design \\*notes\\***.\n"
                "Use `Apps → Add to batch` on a message to add it. "
                "Empty batches are visible with "
                "`/batches all_locations:true`."
            ),
            ephemeral=True,
        )

    async def test_create_batch_rejects_overlong_title(self) -> None:
        interaction = FakeInteraction()

        with patch.object(
            bot,
            "create_saved_batch",
            new=AsyncMock(),
        ) as create_batch:
            await bot.create_batch_command.callback(
                interaction,
                title="x" * 101,
            )

        create_batch.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once_with(
            "Batch titles cannot exceed 100 characters.",
            ephemeral=True,
        )


class ManualBatchPickerTests(unittest.IsolatedAsyncioTestCase):
    def create_view(self) -> bot.ManualBatchPickerView:
        return bot.ManualBatchPickerView(
            owner_user_id=42,
            message=FakeMessage(),
            recent_batches=[
                recent_batch(17, title="Architecture", message_count=2),
                recent_batch(18, title=None, message_count=0),
            ],
        )

    async def test_context_action_loads_owned_recent_batches(self) -> None:
        interaction = FakeInteraction()
        message = FakeMessage()
        rows = [recent_batch(17, title="Architecture", message_count=2)]

        with (
            patch.object(
                bot,
                "get_recent_saved_batches",
                new=AsyncMock(return_value=rows),
            ) as get_batches,
            patch.object(
                bot,
                "is_user_ignored",
                new=AsyncMock(),
            ) as is_ignored,
        ):
            await bot.add_to_batch_context_menu.callback(
                interaction,
                message,
            )

        get_batches.assert_awaited_once_with(
            saved_by_user_id="42",
            limit=25,
        )
        is_ignored.assert_not_awaited()
        send_call = interaction.response.send_message.await_args
        self.assertEqual(
            send_call.args[0],
            "Choose a batch for this message, or create a new one.",
        )
        self.assertTrue(send_call.kwargs["ephemeral"])
        view = send_call.kwargs["view"]
        self.assertIsInstance(view, bot.ManualBatchPickerView)
        select = next(
            child
            for child in view.children
            if isinstance(child, bot.RecentBatchSelect)
        )
        self.assertEqual(select.options[0].label, "Architecture")
        self.assertIn("2 messages", select.options[0].description)

    async def test_context_action_without_batches_offers_creation(self) -> None:
        interaction = FakeInteraction()

        with patch.object(
            bot,
            "get_recent_saved_batches",
            new=AsyncMock(return_value=[]),
        ):
            await bot.add_to_batch_context_menu.callback(
                interaction,
                FakeMessage(),
            )

        send_call = interaction.response.send_message.await_args
        self.assertEqual(
            send_call.args[0],
            "You have no batches yet. Create one for this message.",
        )
        view = send_call.kwargs["view"]
        self.assertFalse(
            any(isinstance(child, discord.ui.Select) for child in view.children)
        )
        self.assertIsNotNone(view.create_new_batch)

    async def test_picker_is_owner_scoped(self) -> None:
        view = self.create_view()
        other_interaction = FakeInteraction(user_id=99)

        self.assertTrue(await view.interaction_check(FakeInteraction()))
        self.assertFalse(await view.interaction_check(other_interaction))
        other_interaction.response.send_message.assert_awaited_once_with(
            "This batch picker belongs to another user.",
            ephemeral=True,
        )

    async def test_existing_batch_add_passes_complete_message_metadata(
        self,
    ) -> None:
        view = self.create_view()
        interaction = FakeInteraction()
        result = bot.ManualBatchAddResult(
            batch_id=17,
            saved_message_id=7,
            message_was_saved=True,
            association_was_created=True,
            position=2,
        )

        with patch.object(
            bot,
            "add_message_to_saved_batch",
            new=AsyncMock(return_value=result),
        ) as add_message:
            await view.add_to_existing_batch(
                interaction,
                batch_id=17,
                title="Architecture",
            )

        kwargs = add_message.await_args.kwargs
        self.assertEqual(kwargs["batch_id"], 17)
        self.assertEqual(kwargs["saved_by_user_id"], "42")
        self.assertEqual(kwargs["message"].message_id, "100")
        self.assertEqual(kwargs["message"].guild_name, "Test Guild")
        self.assertEqual(kwargs["message"].channel_name, "general")
        self.assertEqual(kwargs["message"].position, 0)
        self.assertEqual(
            kwargs["message"].attachments[0].attachment_id,
            "501",
        )
        interaction.response.edit_message.assert_awaited_once_with(
            content=(
                "Added the message to **Architecture** at position 3.\n"
                "The message was saved as `UNREAD`."
            ),
            view=None,
        )

    async def test_duplicate_and_stale_batch_have_clear_responses(self) -> None:
        duplicate_view = self.create_view()
        duplicate_interaction = FakeInteraction()
        duplicate_result = bot.ManualBatchAddResult(
            batch_id=17,
            saved_message_id=7,
            message_was_saved=False,
            association_was_created=False,
            position=0,
        )

        with patch.object(
            bot,
            "add_message_to_saved_batch",
            new=AsyncMock(return_value=duplicate_result),
        ):
            await duplicate_view.add_to_existing_batch(
                duplicate_interaction,
                batch_id=17,
                title="Architecture",
            )

        duplicate_interaction.response.edit_message.assert_awaited_once_with(
            content="This message already belongs to **Architecture**.",
            view=None,
        )

        stale_view = self.create_view()
        stale_interaction = FakeInteraction()

        with patch.object(
            bot,
            "add_message_to_saved_batch",
            new=AsyncMock(side_effect=bot.SavedBatchNotFoundError),
        ):
            await stale_view.add_to_existing_batch(
                stale_interaction,
                batch_id=17,
                title="Architecture",
            )

        stale_interaction.response.edit_message.assert_awaited_once_with(
            content=(
                "That batch no longer exists. Open `Add to batch` "
                "again to refresh the list."
            ),
            view=None,
        )

    async def test_create_button_opens_owner_scoped_modal(self) -> None:
        view = self.create_view()
        interaction = FakeInteraction()

        await view.create_new_batch.callback(interaction)

        interaction.response.send_modal.assert_awaited_once()
        modal = interaction.response.send_modal.await_args.args[0]
        self.assertIsInstance(modal, bot.CreateBatchWithMessageModal)
        self.assertEqual(modal.owner_user_id, 42)
        self.assertFalse(modal.batch_title.required)
        self.assertEqual(modal.batch_title.max_length, 100)

    async def test_modal_atomically_creates_batch_with_message(self) -> None:
        view = self.create_view()
        modal = bot.CreateBatchWithMessageModal(picker_view=view)
        modal.batch_title._value = "New collection"
        interaction = FakeInteraction()
        result = bot.ManualBatchAddResult(
            batch_id=21,
            saved_message_id=8,
            message_was_saved=False,
            association_was_created=True,
            position=0,
        )

        with patch.object(
            bot,
            "create_saved_batch_with_message",
            new=AsyncMock(return_value=result),
        ) as create_with_message:
            await modal.on_submit(interaction)

        kwargs = create_with_message.await_args.kwargs
        self.assertEqual(kwargs["saved_by_user_id"], "42")
        self.assertEqual(kwargs["title"], "New collection")
        self.assertEqual(kwargs["message"].message_id, "100")
        interaction.response.edit_message.assert_awaited_once_with(
            content=(
                "Added the message to **New collection** at position 1.\n"
                "Its existing saved-message record was reused."
            ),
            view=None,
        )

    async def test_modal_rejects_another_user(self) -> None:
        view = self.create_view()
        modal = bot.CreateBatchWithMessageModal(picker_view=view)
        interaction = FakeInteraction(user_id=99)

        with patch.object(
            bot,
            "create_saved_batch_with_message",
            new=AsyncMock(),
        ) as create_with_message:
            await modal.on_submit(interaction)

        create_with_message.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once_with(
            "This batch form belongs to another user.",
            ephemeral=True,
        )


if __name__ == "__main__":
    unittest.main()
