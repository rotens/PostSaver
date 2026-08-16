"""Convert Discord messages into database-ready values."""

import discord

from database import AttachmentToSave, MessageToSave


MAX_RANGE_MESSAGES_TO_SCAN = 1000


class RangeTooLargeError(ValueError):
    """Raised when a selected message range exceeds the scan limit."""


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


def prepare_message_to_save(
    message: discord.Message,
    *,
    position: int,
) -> MessageToSave:
    guild_name, channel_name = get_message_location_names(message)

    return MessageToSave(
        message_id=str(message.id),
        guild_id=str(message.guild.id) if message.guild else None,
        guild_name=guild_name,
        channel_id=str(message.channel.id),
        channel_name=channel_name,
        author_id=str(message.author.id),
        author_name=str(message.author),
        content=message.content,
        jump_url=message.jump_url,
        message_created_at=message.created_at.isoformat(),
        position=position,
        attachments=prepare_attachments_to_save(message.attachments),
    )


def prepare_messages_to_save(
    messages: list[discord.Message],
) -> list[MessageToSave]:
    return [
        prepare_message_to_save(message, position=position)
        for position, message in enumerate(messages)
    ]
