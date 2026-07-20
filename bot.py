import os

import discord
from discord import app_commands
from dotenv import load_dotenv

from database import (
    count_saved_messages,
    delete_saved_message,
    get_saved_messages,
    initialize_database,
    save_unread_message,
    update_saved_message_status,
)


load_dotenv()


SAVED_MESSAGES_PAGE_SIZE = 5


class ReadingBot(discord.Client):
    def __init__(self) -> None:
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        print("setup_hook started")

        await initialize_database()
        print("database initialized")

        synced = await self.tree.sync()
        print("synced commands:", [command.name for command in synced])


bot = ReadingBot()


@bot.tree.context_menu(name="Save as UNREAD")
async def save_as_unread(
    interaction: discord.Interaction,
    message: discord.Message,
) -> None:
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
