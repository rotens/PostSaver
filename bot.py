import os

import discord
from discord import app_commands
from dotenv import load_dotenv

from database import (
    MessageToSave,
    PendingRangeChangedError,
    count_saved_messages,
    delete_pending_range_if_matches,
    delete_saved_message,
    get_ignored_user_ids,
    get_pending_range,
    get_saved_messages,
    ignore_user,
    initialize_database,
    is_user_ignored,
    save_message_range_as_batch,
    save_unread_message,
    set_pending_range_start,
    unignore_all_users,
    unignore_user,
    update_saved_message_status,
)


load_dotenv()


SAVED_MESSAGES_PAGE_SIZE = 5
MAX_RANGE_MESSAGES = 100


class ReadingBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True

        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        print("setup_hook started")

        await initialize_database()
        print("database initialized")

        synced = await self.tree.sync()
        print("synced commands:", [command.name for command in synced])


bot = ReadingBot()


class RangeTooLargeError(ValueError):
    pass


async def get_messages_in_range(
    start_message: discord.Message,
    end_message: discord.Message,
    *,
    max_messages: int = MAX_RANGE_MESSAGES,
) -> list[discord.Message]:
    if max_messages < 1:
        raise ValueError("The maximum range size must be at least one")

    if start_message.id == end_message.id:
        return [start_message]

    if start_message.id < end_message.id:
        older_message = start_message
        newer_message = end_message
    else:
        older_message = end_message
        newer_message = start_message

    messages_between = [
        message
        async for message in end_message.channel.history(
            limit=max_messages - 1,
            after=discord.Object(id=older_message.id),
            before=discord.Object(id=newer_message.id),
            oldest_first=True,
        )
    ]

    messages = [older_message, *messages_between, newer_message]

    if len(messages) > max_messages:
        raise RangeTooLargeError(
            f"A message range cannot contain more than {max_messages} messages"
        )

    return messages


def prepare_messages_to_save(
    messages: list[discord.Message],
) -> list[MessageToSave]:
    return [
        MessageToSave(
            message_id=str(message.id),
            guild_id=str(message.guild.id) if message.guild else None,
            channel_id=str(message.channel.id),
            author_id=str(message.author.id),
            author_name=str(message.author),
            content=message.content,
            jump_url=message.jump_url,
            message_created_at=message.created_at.isoformat(),
            position=position,
        )
        for position, message in enumerate(messages)
    ]


async def complete_message_range(
    *,
    interaction: discord.Interaction,
    end_message: discord.Message,
    expected_start_message_id: str,
    batch_title: str,
) -> None:
    saved_by_user_id = str(interaction.user.id)
    pending_range = await get_pending_range(
        saved_by_user_id=saved_by_user_id,
    )

    if (
        pending_range is None
        or pending_range["start_message_id"] != expected_start_message_id
    ):
        await interaction.edit_original_response(
            content=(
                "Your range start changed before this range was saved. "
                "Open `Save through range end` again."
            ),
        )
        return

    end_guild_id = str(end_message.guild.id) if end_message.guild else None

    if (
        pending_range["guild_id"] != end_guild_id
        or pending_range["channel_id"] != str(end_message.channel.id)
    ):
        await interaction.edit_original_response(
            content="The range start and end must be in the same channel.",
        )
        return

    if expected_start_message_id == str(end_message.id):
        start_message = end_message
    else:
        try:
            start_message = await end_message.channel.fetch_message(
                int(expected_start_message_id)
            )
        except discord.NotFound:
            was_cleared = await delete_pending_range_if_matches(
                saved_by_user_id=saved_by_user_id,
                expected_start_message_id=expected_start_message_id,
            )
            response = "The range-start message no longer exists."

            if was_cleared:
                response += " The pending range was cleared."

            await interaction.edit_original_response(content=response)
            return
        except discord.Forbidden:
            await interaction.edit_original_response(
                content=(
                    "I cannot access the range-start message. "
                    "The pending range was kept."
                ),
            )
            return
        except discord.HTTPException:
            await interaction.edit_original_response(
                content=(
                    "Discord could not provide the range-start message. "
                    "Please try again; the pending range was kept."
                ),
            )
            return

    try:
        messages_in_range = await get_messages_in_range(
            start_message,
            end_message,
        )
    except RangeTooLargeError:
        await interaction.edit_original_response(
            content=(
                f"This range contains more than {MAX_RANGE_MESSAGES} messages. "
                "Nothing was saved and the pending range was kept."
            ),
        )
        return
    except discord.Forbidden:
        await interaction.edit_original_response(
            content=(
                "I cannot read this channel's message history. "
                "Nothing was saved and the pending range was kept."
            ),
        )
        return
    except discord.HTTPException:
        await interaction.edit_original_response(
            content=(
                "Discord could not provide the message history. "
                "Please try again; the pending range was kept."
            ),
        )
        return

    ignored_user_ids = await get_ignored_user_ids(
        saved_by_user_id=saved_by_user_id,
    )
    messages_to_save = [
        message
        for message in messages_in_range
        if str(message.author.id) not in ignored_user_ids
    ]
    ignored_count = len(messages_in_range) - len(messages_to_save)

    if not messages_to_save:
        was_cleared = await delete_pending_range_if_matches(
            saved_by_user_id=saved_by_user_id,
            expected_start_message_id=expected_start_message_id,
        )

        if not was_cleared:
            await interaction.edit_original_response(
                content=(
                    "Your range start changed before this range was completed. "
                    "The newer pending range was kept."
                ),
            )
            return

        await interaction.edit_original_response(
            content=(
                "Range completed.\n"
                f"Messages in range: {len(messages_in_range)}\n"
                "Saved: 0\n"
                "Already saved: 0\n"
                f"Ignored: {ignored_count}\n"
                "No batch was created because every message was ignored."
            ),
        )
        return

    try:
        result = await save_message_range_as_batch(
            saved_by_user_id=saved_by_user_id,
            expected_start_message_id=expected_start_message_id,
            title=batch_title,
            messages=prepare_messages_to_save(messages_to_save),
        )
    except PendingRangeChangedError:
        await interaction.edit_original_response(
            content=(
                "Your range start changed before this range was saved. "
                "The newer pending range was kept."
            ),
        )
        return

    normalized_title = batch_title.strip()

    if normalized_title:
        batch_label = discord.utils.escape_markdown(normalized_title)
    else:
        batch_label = f"Untitled batch #{result.batch_id}"

    await interaction.edit_original_response(
        content=(
            "Range completed.\n"
            f"Batch: {batch_label}\n"
            f"Messages in range: {len(messages_in_range)}\n"
            f"Saved: {result.saved_count}\n"
            f"Already saved: {result.already_saved_count}\n"
            f"Ignored: {ignored_count}"
        ),
    )


class SaveRangeModal(discord.ui.Modal, title="Save message range"):
    batch_title = discord.ui.TextInput(
        label="Title",
        placeholder="Optional title for this message range",
        required=False,
        max_length=100,
    )

    def __init__(
        self,
        *,
        owner_user_id: int,
        expected_start_message_id: str,
        end_message: discord.Message,
    ) -> None:
        super().__init__(timeout=600)
        self.owner_user_id = owner_user_id
        self.expected_start_message_id = expected_start_message_id
        self.end_message = end_message

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_user_id:
            await interaction.response.send_message(
                "This range form belongs to another user.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )
        await complete_message_range(
            interaction=interaction,
            end_message=self.end_message,
            expected_start_message_id=self.expected_start_message_id,
            batch_title=self.batch_title.value,
        )


@bot.tree.context_menu(name="Save as UNREAD")
async def save_as_unread(
    interaction: discord.Interaction,
    message: discord.Message,
) -> None:
    if await is_user_ignored(
        saved_by_user_id=str(interaction.user.id),
        ignored_user_id=str(message.author.id),
    ):
        await interaction.response.send_message(
            (
                f"Message not saved because you are ignoring "
                f"messages from {message.author.mention}."
            ),
            ephemeral=True,
        )
        return

    guild_id = str(message.guild.id) if message.guild else None

    was_inserted = await save_unread_message(
        saved_by_user_id=str(interaction.user.id),
        message_id=str(message.id),
        guild_id=guild_id,
        channel_id=str(message.channel.id),
        author_id=str(message.author.id),
        author_name=str(message.author),
        content=message.content,
        jump_url=message.jump_url,
        message_created_at=message.created_at.isoformat(),
    )

    if was_inserted:
        response = f"Saved as UNREAD: {message.jump_url}"
    else:
        response = "This message is already saved."

    await interaction.response.send_message(
        response,
        ephemeral=True,
    )


async def respond_to_ignore_user(
    interaction: discord.Interaction,
    user: discord.User | discord.Member,
) -> None:
    was_added = await ignore_user(
        saved_by_user_id=str(interaction.user.id),
        ignored_user_id=str(user.id),
    )

    if was_added:
        response = f"Messages from {user.mention} will now be ignored."
    else:
        response = f"Messages from {user.mention} are already ignored."

    await interaction.response.send_message(
        response,
        ephemeral=True,
    )


async def respond_to_unignore_user(
    interaction: discord.Interaction,
    user: discord.User | discord.Member,
) -> None:
    was_removed = await unignore_user(
        saved_by_user_id=str(interaction.user.id),
        ignored_user_id=str(user.id),
    )

    if was_removed:
        response = f"Messages from {user.mention} can now be saved again."
    else:
        response = f"Messages from {user.mention} were not being ignored."

    await interaction.response.send_message(
        response,
        ephemeral=True,
    )


@bot.tree.command(
    name="ignore_user",
    description="Ignore a user's messages when saving",
)
@app_commands.describe(
    user="Choose the user whose messages should be ignored",
)
async def ignore_user_messages(
    interaction: discord.Interaction,
    user: discord.User,
) -> None:
    await respond_to_ignore_user(interaction, user)


@bot.tree.command(
    name="unignore_user",
    description="Allow a user's messages to be saved again",
)
@app_commands.describe(
    user="Choose the user whose messages should no longer be ignored",
)
async def unignore_user_messages(
    interaction: discord.Interaction,
    user: discord.User,
) -> None:
    await respond_to_unignore_user(interaction, user)


@bot.tree.command(
    name="unignore_all",
    description="Stop ignoring messages from all users",
)
async def unignore_all_user_messages(
    interaction: discord.Interaction,
) -> None:
    removed_count = await unignore_all_users(
        saved_by_user_id=str(interaction.user.id),
    )

    if removed_count == 0:
        response = "Your ignore settings were already at the default."
    else:
        user_label = "user" if removed_count == 1 else "users"
        response = (
            f"Reset your ignore settings for "
            f"{removed_count} {user_label}."
        )

    await interaction.response.send_message(
        response,
        ephemeral=True,
    )


@bot.tree.context_menu(name="Ignore user's messages")
async def ignore_user_context_menu(
    interaction: discord.Interaction,
    user: discord.User,
) -> None:
    await respond_to_ignore_user(interaction, user)


@bot.tree.context_menu(name="Unignore user's messages")
async def unignore_user_context_menu(
    interaction: discord.Interaction,
    user: discord.User,
) -> None:
    await respond_to_unignore_user(interaction, user)


@bot.tree.context_menu(name="Ignore message author")
async def ignore_message_author_context_menu(
    interaction: discord.Interaction,
    message: discord.Message,
) -> None:
    await respond_to_ignore_user(interaction, message.author)


@bot.tree.context_menu(name="Unignore message author")
async def unignore_message_author_context_menu(
    interaction: discord.Interaction,
    message: discord.Message,
) -> None:
    await respond_to_unignore_user(interaction, message.author)


@bot.tree.context_menu(name="Set range start")
async def set_range_start_context_menu(
    interaction: discord.Interaction,
    message: discord.Message,
) -> None:
    guild_id = str(message.guild.id) if message.guild else None

    await set_pending_range_start(
        saved_by_user_id=str(interaction.user.id),
        guild_id=guild_id,
        channel_id=str(message.channel.id),
        start_message_id=str(message.id),
    )

    await interaction.response.send_message(
        (
            f"Range start set: {message.jump_url}\n"
            "Selecting another start will replace this one."
        ),
        ephemeral=True,
    )


@bot.tree.context_menu(name="Save through range end")
async def save_through_range_end_context_menu(
    interaction: discord.Interaction,
    message: discord.Message,
) -> None:
    pending_range = await get_pending_range(
        saved_by_user_id=str(interaction.user.id),
    )

    if pending_range is None:
        await interaction.response.send_message(
            "Set a range start before selecting a range end.",
            ephemeral=True,
        )
        return

    guild_id = str(message.guild.id) if message.guild else None

    if (
        pending_range["guild_id"] != guild_id
        or pending_range["channel_id"] != str(message.channel.id)
    ):
        await interaction.response.send_message(
            "The range start and end must be in the same channel.",
            ephemeral=True,
        )
        return

    await interaction.response.send_modal(
        SaveRangeModal(
            owner_user_id=interaction.user.id,
            expected_start_message_id=pending_range["start_message_id"],
            end_message=message,
        )
    )


class SavedMessageView(discord.ui.View):
    def __init__(
        self,
        *,
        record_id: int,
        owner_user_id: int,
        jump_url: str,
        current_status: str,
        page_number: int,
        total_pages: int,
    ) -> None:
        super().__init__(timeout=600)

        self.record_id = record_id
        self.owner_user_id = owner_user_id
        self.current_status = current_status
        self.page_number = page_number
        self.total_pages = total_pages

        open_button = discord.ui.Button(
            label="Open message",
            style=discord.ButtonStyle.link,
            url=jump_url,
        )
        self.add_item(open_button)

        self.refresh_buttons()

    def refresh_buttons(self) -> None:
        for item in self.children:
            if not isinstance(item, discord.ui.Button):
                continue

            if item.custom_id == "saved:read_keep":
                item.disabled = self.current_status == "READ_KEEP"

            elif item.custom_id == "saved:unread":
                item.disabled = self.current_status == "UNREAD"

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if interaction.user.id == self.owner_user_id:
            return True

        await interaction.response.send_message(
            "This saved-message panel belongs to another user.",
            ephemeral=True,
        )
        return False

    async def set_status(
        self,
        interaction: discord.Interaction,
        status: str,
    ) -> None:
        was_updated = await update_saved_message_status(
            record_id=self.record_id,
            saved_by_user_id=str(self.owner_user_id),
            status=status,
        )

        if not was_updated:
            self.stop()

            await interaction.response.edit_message(
                content="This record no longer exists in the database.",
                embed=None,
                view=None,
            )
            return

        self.current_status = status
        self.refresh_buttons()

        embed = interaction.message.embeds[0]
        embed.set_footer(
            text=(
                f"Status: {status} | "
                f"Page {self.page_number}/{self.total_pages}"
            ),
        )

        await interaction.response.edit_message(
            embed=embed,
            view=self,
        )

    @discord.ui.button(
        label="READ_KEEP",
        style=discord.ButtonStyle.secondary,
        custom_id="saved:read_keep",
    )
    async def mark_read_keep(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.set_status(interaction, "READ_KEEP")

    @discord.ui.button(
        label="UNREAD",
        style=discord.ButtonStyle.primary,
        custom_id="saved:unread",
    )
    async def mark_unread(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.set_status(interaction, "UNREAD")

    @discord.ui.button(
        label="DELETE",
        style=discord.ButtonStyle.danger,
        custom_id="saved:delete",
    )
    async def delete_record(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        was_deleted = await delete_saved_message(
            record_id=self.record_id,
            saved_by_user_id=str(self.owner_user_id),
        )

        self.stop()

        if was_deleted:
            message = "The saved message was deleted from the database."
        else:
            message = "This record was already deleted."

        await interaction.response.edit_message(
            content=message,
            embed=None,
            view=None,
        )


@bot.tree.command(
    name="saved",
    description="Show your saved Discord messages",
)
@app_commands.describe(
    status="Choose which message status to show",
    page="Choose which page to show",
)
@app_commands.choices(
    status=[
        app_commands.Choice(
            name="Unread",
            value="UNREAD",
        ),
        app_commands.Choice(
            name="Read and kept",
            value="READ_KEEP",
        ),
        app_commands.Choice(
            name="All",
            value="ALL",
        ),
    ],
)
async def show_saved_messages(
    interaction: discord.Interaction,
    status: app_commands.Choice[str] | None = None,
    page: app_commands.Range[int, 1] = 1,
) -> None:
    
    print("/saved handler started")
    print("user:", interaction.user.id)
    print("status:", status)

    selected_status = status.value if status else "UNREAD"

    await interaction.response.defer(ephemeral=True)

    total_records = await count_saved_messages(
        saved_by_user_id=str(interaction.user.id),
        status=selected_status,
    )

    if total_records == 0:
        await interaction.edit_original_response(
            content=(
                f"You have no saved messages "
                f"with status `{selected_status}`."
            ),
        )
        return

    total_pages = (
        total_records + SAVED_MESSAGES_PAGE_SIZE - 1
    ) // SAVED_MESSAGES_PAGE_SIZE

    if page > total_pages:
        await interaction.edit_original_response(
            content=(
                f"Page `{page}` does not exist. "
                f"You have {total_pages} page(s) "
                f"with status `{selected_status}`."
            ),
        )
        return

    rows = await get_saved_messages(
        saved_by_user_id=str(interaction.user.id),
        status=selected_status,
        limit=SAVED_MESSAGES_PAGE_SIZE,
        offset=(page - 1) * SAVED_MESSAGES_PAGE_SIZE,
    )

    for index, row in enumerate(rows):
        content = row["content"].strip()

        if not content:
            content = "*Message has no text content.*"

        if len(content) > 1000:
            content = content[:997] + "..."

        embed = discord.Embed(
            title=row["author_name"],
            description=content,
        )

        embed.add_field(
            name="Created",
            value=row["message_created_at"],
            inline=False,
        )

        embed.set_footer(
            text=(
                f'Status: {row["status"]} | '
                f"Page {page}/{total_pages}"
            ),
        )

        view = SavedMessageView(
            record_id=row["id"],
            owner_user_id=interaction.user.id,
            jump_url=row["jump_url"],
            current_status=row["status"],
            page_number=page,
            total_pages=total_pages,
        )

        if index == 0:
            await interaction.edit_original_response(
                embed=embed,
                view=view,
            )
        else:
            await interaction.followup.send(
                embed=embed,
                view=view,
                ephemeral=True,
            )
    

token = os.getenv("DISCORD_TOKEN")

if not token:
    raise RuntimeError("Missing DISCORD_TOKEN variable")

bot.run(token)
