import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

import discord
from discord import app_commands
from dotenv import load_dotenv

from database import (
    AttachmentToSave,
    MessageToSave,
    PendingRangeChangedError,
    SavedMessageFilters,
    count_saved_batches,
    count_saved_messages_in_batch,
    count_saved_messages,
    delete_pending_range_if_matches,
    delete_saved_message,
    get_ignored_user_ids,
    get_attachments_for_saved_messages,
    get_pending_range,
    get_saved_author_autocomplete_choices,
    get_saved_batches,
    get_saved_channel_autocomplete_choices,
    get_saved_guild_autocomplete_choices,
    get_saved_messages_in_batch,
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
SAVED_BATCHES_PAGE_SIZE = 5
BATCH_DETAIL_PAGE_SIZE = 5
BATCH_PREVIEW_CONTENT_LIMIT = 350
BATCH_DETAIL_CONTENT_LIMIT = 400
SAVED_ATTACHMENT_FIELD_VALUE_LIMIT = 1024
BATCH_ATTACHMENT_FIELD_VALUE_LIMIT = 400
MAX_RANGE_MESSAGES_TO_SCAN = 1000
MAX_SAVED_MESSAGES_PER_RANGE = 300


class ReadingBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True

        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(
            self,
            allowed_installs=app_commands.AppInstallationType(
                guild=True,
                user=True,
            ),
            allowed_contexts=app_commands.AppCommandContext(
                guild=True,
                dm_channel=True,
                private_channel=True,
            ),
        )

    async def setup_hook(self) -> None:
        print("setup_hook started")

        await initialize_database()
        print("database initialized")

        synced = await self.tree.sync()
        print("synced commands:", [command.name for command in synced])


bot = ReadingBot()


class RangeTooLargeError(ValueError):
    pass


@dataclass(frozen=True)
class SavedBatchDetailPage:
    current_page: int
    total_pages: int
    total_messages: int
    embeds: tuple[discord.Embed, ...]


def _parse_date_filter(
    value: str | None,
    *,
    field_name: str,
) -> date | None:
    if value is None:
        return None

    normalized_value = value.strip()

    try:
        parsed_date = date.fromisoformat(normalized_value)
    except ValueError as error:
        raise ValueError(
            f"`{field_name}` must use the `YYYY-MM-DD` format."
        ) from error

    if parsed_date.isoformat() != normalized_value:
        raise ValueError(
            f"`{field_name}` must use the `YYYY-MM-DD` format."
        )

    return parsed_date


def parse_saved_message_date_range(
    *,
    date_from: str | None,
    date_to: str | None,
) -> tuple[str | None, str | None]:
    parsed_from = _parse_date_filter(date_from, field_name="date_from")
    parsed_to = _parse_date_filter(date_to, field_name="date_to")

    if parsed_from and parsed_to and parsed_from > parsed_to:
        raise ValueError("`date_from` cannot be later than `date_to`.")

    created_from = (
        datetime.combine(parsed_from, time.min, tzinfo=timezone.utc)
        .isoformat()
        if parsed_from
        else None
    )

    if parsed_to:
        try:
            day_after_to = parsed_to + timedelta(days=1)
        except OverflowError as error:
            raise ValueError(
                "`date_to` must be earlier than `9999-12-31`."
            ) from error

        created_before = datetime.combine(
            day_after_to,
            time.min,
            tzinfo=timezone.utc,
        ).isoformat()
    else:
        created_before = None

    return created_from, created_before


def _normalize_discord_id(
    value: str | None,
    *,
    field_name: str,
) -> str | None:
    if value is None:
        return None

    normalized_value = value.strip()

    if not normalized_value.isdecimal():
        raise ValueError(f"`{field_name}` must be a Discord ID.")

    return normalized_value


def create_saved_message_filters(
    *,
    selected_status: str,
    keyword: str | None,
    date_from: str | None,
    date_to: str | None,
    author_id: str | None,
    guild_id: str | None,
    channel_id: str | None,
    all_locations: bool,
    current_guild_id: int | None,
    current_channel_id: int | None,
) -> SavedMessageFilters:
    normalized_author_id = _normalize_discord_id(
        author_id,
        field_name="author_id",
    )
    normalized_guild_id = _normalize_discord_id(
        guild_id,
        field_name="guild_id",
    )
    normalized_channel_id = _normalize_discord_id(
        channel_id,
        field_name="channel_id",
    )

    if all_locations and (
        normalized_guild_id is not None
        or normalized_channel_id is not None
    ):
        raise ValueError(
            "`all_locations` cannot be combined with `guild_id` "
            "or `channel_id`."
        )

    if not all_locations and (
        normalized_guild_id is None
        and normalized_channel_id is None
    ):
        normalized_guild_id = (
            str(current_guild_id) if current_guild_id is not None else None
        )
        normalized_channel_id = (
            str(current_channel_id)
            if current_channel_id is not None
            else None
        )

    created_from, created_before = parse_saved_message_date_range(
        date_from=date_from,
        date_to=date_to,
    )
    normalized_keyword = keyword.strip() if keyword else None

    return SavedMessageFilters(
        status=selected_status,
        keyword=normalized_keyword or None,
        created_from=created_from,
        created_before=created_before,
        author_id=normalized_author_id,
        channel_id=normalized_channel_id,
        guild_id=normalized_guild_id,
    )


def format_active_saved_filters(
    *,
    filters: SavedMessageFilters,
    date_from: str | None,
    date_to: str | None,
) -> str:
    parts = [f"Status: `{filters.status}`"]

    if filters.keyword:
        escaped_keyword = discord.utils.escape_markdown(filters.keyword)
        escaped_keyword = escaped_keyword.replace("`", "\\`")
        parts.append(f"Keyword: `{escaped_keyword}`")

    if filters.author_id:
        parts.append(f"Author: <@{filters.author_id}>")

    if filters.channel_id:
        parts.append(f"Channel: <#{filters.channel_id}>")

    if filters.guild_id:
        parts.append(f"Server ID: `{filters.guild_id}`")

    if filters.channel_id is None and filters.guild_id is None:
        parts.append("Location: all")

    normalized_date_from = date_from.strip() if date_from else None
    normalized_date_to = date_to.strip() if date_to else None

    if normalized_date_from or normalized_date_to:
        parts.append(
            "Original date: "
            f"`{normalized_date_from or 'any'}` to "
            f"`{normalized_date_to or 'any'}`"
        )

    return "Active filters: " + " • ".join(parts)


def _autocomplete_choice_name(label: str, discord_id: str) -> str:
    suffix = f" ({discord_id})"
    available_label_length = 100 - len(suffix)

    if available_label_length < 1:
        return discord_id[:100]

    return f"{label[:available_label_length]}{suffix}"


def prepare_attachments_to_save(
    attachments: list[discord.Attachment],
) -> tuple[AttachmentToSave, ...]:
    return tuple(
        AttachmentToSave(
            attachment_id=str(attachment.id),
            filename=attachment.filename,
            url=attachment.url,
            proxy_url=attachment.proxy_url,
            content_type=attachment.content_type,
            size=attachment.size,
            description=attachment.description,
            width=attachment.width,
            height=attachment.height,
            position=position,
        )
        for position, attachment in enumerate(attachments)
    )


def get_message_location_names(
    message: discord.Message,
) -> tuple[str | None, str | None]:
    guild_name = (
        str(message.guild.name)
        if message.guild and getattr(message.guild, "name", None)
        else None
    )
    channel_name_value = getattr(message.channel, "name", None)
    channel_name = (
        str(channel_name_value)
        if channel_name_value
        else None
    )

    return guild_name, channel_name


def add_location_to_embed(
    embed: discord.Embed,
    *,
    guild_id: str | None,
    guild_name: str | None,
    channel_id: str,
    channel_name: str | None,
) -> None:
    if guild_id is None:
        server_display = "Direct message"
    elif guild_name:
        server_display = discord.utils.escape_markdown(guild_name)
    else:
        server_display = f"ID: {guild_id}"

    if channel_name:
        escaped_channel_name = discord.utils.escape_markdown(channel_name)
        channel_display = (
            escaped_channel_name
            if guild_id is None
            else f"#{escaped_channel_name}"
        )
    else:
        channel_display = f"ID: {channel_id}"

    embed.add_field(
        name="Server",
        value=server_display,
        inline=True,
    )
    embed.add_field(
        name="Channel",
        value=channel_display,
        inline=True,
    )


def format_file_size(size: int) -> str:
    value = float(size)

    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            if unit == "B":
                return f"{int(value)} {unit}"

            formatted_value = f"{value:.1f}".rstrip("0").rstrip(".")
            return f"{formatted_value} {unit}"

        value /= 1024

    raise RuntimeError("Failed to format attachment size")


def format_attachment_list(
    attachments,
    *,
    max_length: int,
) -> str:
    if max_length < 1:
        raise ValueError("Attachment display limit must be positive")

    lines = []

    for attachment in attachments:
        filename = " ".join(attachment["filename"].splitlines()).strip()

        if not filename:
            filename = "Unnamed attachment"

        filename = discord.utils.escape_markdown(filename)
        filename = filename.replace("[", "\\[").replace("]", "\\]")
        line = (
            f'[{filename}]({attachment["url"]}) — '
            f'{format_file_size(attachment["size"])}'
        )
        candidate = "\n".join([*lines, line])

        if len(candidate) > max_length:
            break

        lines.append(line)

    omitted_count = len(attachments) - len(lines)

    while omitted_count:
        noun = "attachment" if omitted_count == 1 else "attachments"
        omission = f"*{omitted_count} more {noun} omitted.*"
        candidate = "\n".join([*lines, omission])

        if len(candidate) <= max_length:
            return candidate

        if not lines:
            return omission[:max_length]

        lines.pop()
        omitted_count += 1

    return "\n".join(lines)


def add_attachments_to_embed(
    embed: discord.Embed,
    attachments,
    *,
    field_value_limit: int,
) -> None:
    if not attachments:
        return

    embed.add_field(
        name=f"Attachments ({len(attachments)})",
        value=format_attachment_list(
            attachments,
            max_length=field_value_limit,
        ),
        inline=False,
    )

    image_attachment = next(
        (
            attachment
            for attachment in attachments
            if attachment["content_type"]
            and attachment["content_type"].lower().startswith("image/")
        ),
        None,
    )

    if image_attachment is not None:
        embed.set_image(
            url=image_attachment["proxy_url"] or image_attachment["url"],
        )


async def get_messages_in_range(
    start_message: discord.Message,
    end_message: discord.Message,
    *,
    max_messages: int = MAX_RANGE_MESSAGES_TO_SCAN,
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
    messages_to_save = []

    for position, message in enumerate(messages):
        guild_name, channel_name = get_message_location_names(message)
        messages_to_save.append(
            MessageToSave(
                message_id=str(message.id),
                guild_id=(
                    str(message.guild.id) if message.guild else None
                ),
                guild_name=guild_name,
                channel_id=str(message.channel.id),
                channel_name=channel_name,
                author_id=str(message.author.id),
                author_name=str(message.author),
                content=message.content,
                jump_url=message.jump_url,
                message_created_at=message.created_at.isoformat(),
                position=position,
                attachments=prepare_attachments_to_save(
                    message.attachments
                ),
            )
        )

    return messages_to_save


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
                "This range spans more than "
                f"{MAX_RANGE_MESSAGES_TO_SCAN} Discord messages. "
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

    if len(messages_to_save) > MAX_SAVED_MESSAGES_PER_RANGE:
        await interaction.edit_original_response(
            content=(
                f"This range contains {len(messages_to_save)} messages after "
                "ignored authors are excluded. At most "
                f"{MAX_SAVED_MESSAGES_PER_RANGE} messages can be saved in one "
                "batch. Nothing was saved and the pending range was kept."
            ),
        )
        return

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
    guild_name, channel_name = get_message_location_names(message)

    was_inserted = await save_unread_message(
        saved_by_user_id=str(interaction.user.id),
        message_id=str(message.id),
        guild_id=guild_id,
        guild_name=guild_name,
        channel_id=str(message.channel.id),
        channel_name=channel_name,
        author_id=str(message.author.id),
        author_name=str(message.author),
        content=message.content,
        jump_url=message.jump_url,
        message_created_at=message.created_at.isoformat(),
        attachments=prepare_attachments_to_save(message.attachments),
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


def create_saved_batch_summary_embed(
    row,
    *,
    attachments,
    page: int,
    total_pages: int,
) -> discord.Embed:
    title = row["title"] or f'Untitled batch #{row["id"]}'
    message_count = row["message_count"]

    if message_count == 0:
        description = "*This batch currently has no messages.*"
    else:
        content = row["first_message_content"].strip()

        if not content:
            if attachments:
                content = (
                    "*First message has no text content; "
                    "attachments are listed below.*"
                )
            else:
                content = "*First message has no text content.*"

        if len(content) > BATCH_PREVIEW_CONTENT_LIMIT:
            content = (
                content[: BATCH_PREVIEW_CONTENT_LIMIT - 3]
                + "..."
            )

        description = (
            f'**First message by {row["first_message_author_name"]}**\n'
            f"{content}\n\n"
            f'[Open first message]({row["first_message_jump_url"]})'
        )

    embed = discord.Embed(
        title=title,
        description=description,
    )
    embed.add_field(
        name="Created",
        value=row["created_at"],
        inline=False,
    )
    embed.add_field(
        name="Messages",
        value=str(message_count),
        inline=False,
    )
    if message_count > 0:
        add_location_to_embed(
            embed,
            guild_id=row["first_message_guild_id"],
            guild_name=row["first_message_guild_name"],
            channel_id=row["first_message_channel_id"],
            channel_name=row["first_message_channel_name"],
        )
    add_attachments_to_embed(
        embed,
        attachments,
        field_value_limit=BATCH_ATTACHMENT_FIELD_VALUE_LIMIT,
    )
    embed.set_footer(text=f"Page {page}/{total_pages}")

    return embed


def get_saved_batch_display_title(
    *,
    batch_id: int,
    title: str | None,
) -> str:
    return title or f"Untitled batch #{batch_id}"


def create_saved_batch_message_embed(
    row,
    *,
    attachments,
    message_number: int,
    total_messages: int,
    page: int,
    total_pages: int,
) -> discord.Embed:
    content = row["content"].strip()

    if not content:
        if attachments:
            content = (
                "*This message has no text content; "
                "attachments are listed below.*"
            )
        else:
            content = "*Message has no text content.*"

    if len(content) > BATCH_DETAIL_CONTENT_LIMIT:
        content = content[: BATCH_DETAIL_CONTENT_LIMIT - 3] + "..."

    embed = discord.Embed(
        title=row["author_name"],
        description=(
            f"{content}\n\n"
            f'[Open message]({row["jump_url"]})'
        ),
    )
    embed.add_field(
        name="Created",
        value=row["message_created_at"],
        inline=False,
    )
    add_location_to_embed(
        embed,
        guild_id=row["guild_id"],
        guild_name=row["guild_name"],
        channel_id=row["channel_id"],
        channel_name=row["channel_name"],
    )
    add_attachments_to_embed(
        embed,
        attachments,
        field_value_limit=BATCH_ATTACHMENT_FIELD_VALUE_LIMIT,
    )
    embed.set_footer(
        text=(
            f'Status: {row["status"]} | '
            f"Message {message_number}/{total_messages} | "
            f"Page {page}/{total_pages}"
        )
    )

    return embed


async def get_saved_batch_detail_page(
    *,
    batch_id: int,
    saved_by_user_id: str,
    requested_page: int,
) -> SavedBatchDetailPage | None:
    if requested_page < 1:
        raise ValueError("Page number must be at least 1")

    total_messages = await count_saved_messages_in_batch(
        batch_id=batch_id,
        saved_by_user_id=saved_by_user_id,
    )

    if total_messages == 0:
        return None

    total_pages = (
        total_messages + BATCH_DETAIL_PAGE_SIZE - 1
    ) // BATCH_DETAIL_PAGE_SIZE
    current_page = min(requested_page, total_pages)
    offset = (current_page - 1) * BATCH_DETAIL_PAGE_SIZE
    rows = await get_saved_messages_in_batch(
        batch_id=batch_id,
        saved_by_user_id=saved_by_user_id,
        limit=BATCH_DETAIL_PAGE_SIZE,
        offset=offset,
    )
    attachments_by_message = await get_attachments_for_saved_messages(
        saved_by_user_id=saved_by_user_id,
        saved_message_ids=[row["id"] for row in rows],
    )
    embeds = tuple(
        create_saved_batch_message_embed(
            row,
            attachments=attachments_by_message.get(row["id"], []),
            message_number=offset + index + 1,
            total_messages=total_messages,
            page=current_page,
            total_pages=total_pages,
        )
        for index, row in enumerate(rows)
    )

    return SavedBatchDetailPage(
        current_page=current_page,
        total_pages=total_pages,
        total_messages=total_messages,
        embeds=embeds,
    )


def create_saved_batch_detail_header(
    *,
    batch_id: int,
    title: str | None,
    page: SavedBatchDetailPage,
) -> str:
    display_title = get_saved_batch_display_title(
        batch_id=batch_id,
        title=title,
    )

    return (
        f"Batch: **{discord.utils.escape_markdown(display_title)}** — "
        f"page {page.current_page}/{page.total_pages}"
    )


class BatchDetailView(discord.ui.View):
    def __init__(
        self,
        *,
        batch_id: int,
        owner_user_id: int,
        title: str | None,
        current_page: int,
        total_pages: int,
    ) -> None:
        super().__init__(timeout=600)
        self.batch_id = batch_id
        self.owner_user_id = owner_user_id
        self.title = title
        self.current_page = current_page
        self.total_pages = total_pages
        self.refresh_buttons()

    def refresh_buttons(self) -> None:
        self.previous_page.disabled = self.current_page <= 1
        self.next_page.disabled = self.current_page >= self.total_pages

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if interaction.user.id == self.owner_user_id:
            return True

        await interaction.response.send_message(
            "This batch-detail view belongs to another user.",
            ephemeral=True,
        )
        return False

    async def show_page(
        self,
        interaction: discord.Interaction,
        requested_page: int,
    ) -> None:
        await interaction.response.defer()
        page = await get_saved_batch_detail_page(
            batch_id=self.batch_id,
            saved_by_user_id=str(self.owner_user_id),
            requested_page=requested_page,
        )

        if page is None:
            self.stop()
            await interaction.edit_original_response(
                content="This batch no longer contains any saved messages.",
                embeds=[],
                view=None,
            )
            return

        self.current_page = page.current_page
        self.total_pages = page.total_pages
        self.refresh_buttons()

        await interaction.edit_original_response(
            content=create_saved_batch_detail_header(
                batch_id=self.batch_id,
                title=self.title,
                page=page,
            ),
            embeds=list(page.embeds),
            view=self,
        )

    @discord.ui.button(
        label="Previous",
        style=discord.ButtonStyle.secondary,
        custom_id="batch_detail:previous",
    )
    async def previous_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.show_page(interaction, self.current_page - 1)

    @discord.ui.button(
        label="Next",
        style=discord.ButtonStyle.primary,
        custom_id="batch_detail:next",
    )
    async def next_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.show_page(interaction, self.current_page + 1)


async def open_saved_batch_detail(
    interaction: discord.Interaction,
    *,
    batch_id: int,
    title: str | None,
) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    page = await get_saved_batch_detail_page(
        batch_id=batch_id,
        saved_by_user_id=str(interaction.user.id),
        requested_page=1,
    )

    if page is None:
        await interaction.edit_original_response(
            content="This batch no longer contains any saved messages.",
        )
        return

    view = BatchDetailView(
        batch_id=batch_id,
        owner_user_id=interaction.user.id,
        title=title,
        current_page=page.current_page,
        total_pages=page.total_pages,
    )
    await interaction.edit_original_response(
        content=create_saved_batch_detail_header(
            batch_id=batch_id,
            title=title,
            page=page,
        ),
        embeds=list(page.embeds),
        view=view,
    )


class BatchSummaryView(discord.ui.View):
    def __init__(
        self,
        *,
        batch_id: int,
        owner_user_id: int,
        title: str | None,
        message_count: int,
    ) -> None:
        super().__init__(timeout=600)
        self.batch_id = batch_id
        self.owner_user_id = owner_user_id
        self.title = title
        self.view_batch.disabled = message_count == 0

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if interaction.user.id == self.owner_user_id:
            return True

        await interaction.response.send_message(
            "This batch-summary panel belongs to another user.",
            ephemeral=True,
        )
        return False

    @discord.ui.button(
        label="View batch",
        style=discord.ButtonStyle.primary,
        custom_id="batch_summary:view",
    )
    async def view_batch(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await open_saved_batch_detail(
            interaction,
            batch_id=self.batch_id,
            title=self.title,
        )


@bot.tree.command(
    name="batches",
    description="Show summaries of your saved message batches",
)
@app_commands.describe(
    page="Choose which page to show",
)
async def show_saved_batches(
    interaction: discord.Interaction,
    page: app_commands.Range[int, 1] = 1,
) -> None:
    await interaction.response.defer(ephemeral=True)

    total_batches = await count_saved_batches(
        saved_by_user_id=str(interaction.user.id),
    )

    if total_batches == 0:
        await interaction.edit_original_response(
            content="You have no saved message batches.",
        )
        return

    total_pages = (
        total_batches + SAVED_BATCHES_PAGE_SIZE - 1
    ) // SAVED_BATCHES_PAGE_SIZE

    if page > total_pages:
        await interaction.edit_original_response(
            content=(
                f"Page `{page}` does not exist. "
                f"You have {total_pages} batch page(s)."
            ),
        )
        return

    rows = await get_saved_batches(
        saved_by_user_id=str(interaction.user.id),
        limit=SAVED_BATCHES_PAGE_SIZE,
        offset=(page - 1) * SAVED_BATCHES_PAGE_SIZE,
    )
    first_message_record_ids = [
        row["first_message_record_id"]
        for row in rows
        if row["first_message_record_id"] is not None
    ]
    attachments_by_message = await get_attachments_for_saved_messages(
        saved_by_user_id=str(interaction.user.id),
        saved_message_ids=first_message_record_ids,
    )
    for index, row in enumerate(rows):
        embed = create_saved_batch_summary_embed(
            row,
            attachments=attachments_by_message.get(
                row["first_message_record_id"],
                [],
            ),
            page=page,
            total_pages=total_pages,
        )
        view = BatchSummaryView(
            batch_id=row["id"],
            owner_user_id=interaction.user.id,
            title=row["title"],
            message_count=row["message_count"],
        )

        if index == 0:
            await interaction.edit_original_response(
                content=(
                    "Your saved message batches — "
                    f"page {page}/{total_pages}"
                ),
                embed=embed,
                view=view,
            )
        else:
            await interaction.followup.send(
                embed=embed,
                view=view,
                ephemeral=True,
            )


@bot.tree.command(
    name="saved",
    description="Show your saved Discord messages",
)
@app_commands.describe(
    status="Choose which message status to show",
    page="Choose which page to show",
    keyword="Find text in saved message content",
    date_from="Original message date from YYYY-MM-DD",
    date_to="Original message date through YYYY-MM-DD",
    author_id="Message author (autocomplete or Discord ID)",
    channel_id="Channel (autocomplete or Discord ID)",
    guild_id="Server (autocomplete or Discord ID)",
    all_locations="Search all locations instead of the current channel",
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
    keyword: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    author_id: str | None = None,
    channel_id: str | None = None,
    guild_id: str | None = None,
    all_locations: bool = False,
) -> None:
    print("/saved handler started")
    print("user:", interaction.user.id)
    print("status:", status)

    selected_status = status.value if status else "UNREAD"
    await interaction.response.defer(ephemeral=True)

    try:
        filters = create_saved_message_filters(
            selected_status=selected_status,
            keyword=keyword,
            date_from=date_from,
            date_to=date_to,
            author_id=author_id,
            guild_id=guild_id,
            channel_id=channel_id,
            all_locations=all_locations,
            current_guild_id=interaction.guild_id,
            current_channel_id=interaction.channel_id,
        )
    except ValueError as error:
        await interaction.edit_original_response(content=str(error))
        return

    active_filters = format_active_saved_filters(
        filters=filters,
        date_from=date_from,
        date_to=date_to,
    )

    total_records = await count_saved_messages(
        saved_by_user_id=str(interaction.user.id),
        filters=filters,
    )

    if total_records == 0:
        await interaction.edit_original_response(
            content=(
                "No saved messages match these filters.\n"
                f"{active_filters}"
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
                f"The filtered results have {total_pages} page(s).\n"
                f"{active_filters}"
            ),
        )
        return

    rows = await get_saved_messages(
        saved_by_user_id=str(interaction.user.id),
        filters=filters,
        limit=SAVED_MESSAGES_PAGE_SIZE,
        offset=(page - 1) * SAVED_MESSAGES_PAGE_SIZE,
    )
    attachments_by_message = await get_attachments_for_saved_messages(
        saved_by_user_id=str(interaction.user.id),
        saved_message_ids=[row["id"] for row in rows],
    )

    for index, row in enumerate(rows):
        content = row["content"].strip()
        attachments = attachments_by_message[row["id"]]

        if not content:
            if attachments:
                content = (
                    "*This message has no text content; "
                    "attachments are listed below.*"
                )
            else:
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
        add_location_to_embed(
            embed,
            guild_id=row["guild_id"],
            guild_name=row["guild_name"],
            channel_id=row["channel_id"],
            channel_name=row["channel_name"],
        )
        add_attachments_to_embed(
            embed,
            attachments,
            field_value_limit=SAVED_ATTACHMENT_FIELD_VALUE_LIMIT,
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
                content=(
                    f"Saved messages — page {page}/{total_pages}\n"
                    f"{active_filters}"
                ),
                embed=embed,
                view=view,
            )
        else:
            await interaction.followup.send(
                embed=embed,
                view=view,
                ephemeral=True,
            )


@show_saved_messages.autocomplete("author_id")
async def saved_author_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    rows = await get_saved_author_autocomplete_choices(
        saved_by_user_id=str(interaction.user.id),
        current=current,
    )

    return [
        app_commands.Choice(
            name=_autocomplete_choice_name(
                row["author_name"],
                row["author_id"],
            ),
            value=row["author_id"],
        )
        for row in rows
    ]


@show_saved_messages.autocomplete("channel_id")
async def saved_channel_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    selected_guild_id = getattr(interaction.namespace, "guild_id", None)

    if selected_guild_id is not None:
        selected_guild_id = str(selected_guild_id).strip()

        if not selected_guild_id.isdecimal():
            selected_guild_id = None

    if selected_guild_id is None and not getattr(
        interaction.namespace,
        "all_locations",
        False,
    ):
        selected_guild_id = (
            str(interaction.guild_id)
            if interaction.guild_id is not None
            else None
        )

    rows = await get_saved_channel_autocomplete_choices(
        saved_by_user_id=str(interaction.user.id),
        current=current,
        guild_id=selected_guild_id,
    )
    choices = []

    for row in rows:
        channel_label = (
            f"#{row['channel_name']}"
            if row["channel_name"]
            else "Unknown channel"
        )
        location_label = row["guild_name"] or "Direct message"
        choices.append(
            app_commands.Choice(
                name=_autocomplete_choice_name(
                    f"{channel_label} — {location_label}",
                    row["channel_id"],
                ),
                value=row["channel_id"],
            )
        )

    return choices


@show_saved_messages.autocomplete("guild_id")
async def saved_guild_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    rows = await get_saved_guild_autocomplete_choices(
        saved_by_user_id=str(interaction.user.id),
        current=current,
    )

    return [
        app_commands.Choice(
            name=_autocomplete_choice_name(
                row["guild_name"] or "Unknown server",
                row["guild_id"],
            ),
            value=row["guild_id"],
        )
        for row in rows
    ]


token = os.getenv("DISCORD_TOKEN")

if not token:
    raise RuntimeError("Missing DISCORD_TOKEN variable")

bot.run(token)
