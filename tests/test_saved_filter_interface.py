import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

import bot


class FakeInteraction:
    def __init__(
        self,
        *,
        user_id: int = 42,
        guild_id: int | None = 10,
        channel_id: int | None = 20,
        namespace: object | None = None,
    ) -> None:
        self.user = SimpleNamespace(id=user_id)
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.namespace = namespace or SimpleNamespace()
        self.response = SimpleNamespace(defer=AsyncMock())
        self.followup = SimpleNamespace(send=AsyncMock())
        self.edit_original_response = AsyncMock()


class SavedFilterParsingTests(unittest.TestCase):
    def test_saved_command_registers_filter_and_autocomplete_options(
        self,
    ) -> None:
        parameters = {
            parameter.name: parameter
            for parameter in bot.show_saved_messages.parameters
        }

        self.assertEqual(
            list(parameters),
            [
                "status",
                "page",
                "keyword",
                "date_from",
                "date_to",
                "author_id",
                "channel_id",
                "guild_id",
                "all_locations",
                "sort",
            ],
        )
        self.assertTrue(parameters["author_id"].autocomplete)
        self.assertTrue(parameters["channel_id"].autocomplete)
        self.assertTrue(parameters["guild_id"].autocomplete)
        self.assertEqual(
            [choice.value for choice in parameters["sort"].choices],
            [sort.value for sort in bot.SavedItemSort],
        )

    def test_dates_are_converted_to_inclusive_utc_day_boundaries(self) -> None:
        created_from, created_before = bot.parse_saved_message_date_range(
            date_from="2026-07-01",
            date_to="2026-07-03",
        )

        self.assertEqual(created_from, "2026-07-01T00:00:00+00:00")
        self.assertEqual(created_before, "2026-07-04T00:00:00+00:00")

    def test_invalid_and_reversed_dates_are_rejected(self) -> None:
        invalid_values = (
            ("07-01-2026", None, "`date_from` must use"),
            ("2026-7-01", None, "`date_from` must use"),
            ("2026-07-04", "2026-07-03", "cannot be later"),
            (None, "9999-12-31", "must be earlier"),
        )

        for date_from, date_to, expected_message in invalid_values:
            with self.subTest(date_from=date_from, date_to=date_to):
                with self.assertRaisesRegex(ValueError, expected_message):
                    bot.parse_saved_message_date_range(
                        date_from=date_from,
                        date_to=date_to,
                    )

    def test_omitted_location_defaults_to_current_channel_and_server(
        self,
    ) -> None:
        filters = bot.create_saved_message_filters(
            selected_status="UNREAD",
            keyword="  search me  ",
            date_from=None,
            date_to=None,
            author_id=None,
            guild_id=None,
            channel_id=None,
            all_locations=False,
            current_guild_id=10,
            current_channel_id=20,
        )

        self.assertEqual(
            filters,
            bot.SavedMessageFilters(
                status="UNREAD",
                keyword="search me",
                channel_id="20",
                guild_id="10",
            ),
        )

    def test_explicit_location_and_all_locations_are_distinct(self) -> None:
        channel_filters = bot.create_saved_message_filters(
            selected_status="ALL",
            keyword=None,
            date_from=None,
            date_to=None,
            author_id="30",
            guild_id=None,
            channel_id="40",
            all_locations=False,
            current_guild_id=10,
            current_channel_id=20,
        )
        all_filters = bot.create_saved_message_filters(
            selected_status="ALL",
            keyword=None,
            date_from=None,
            date_to=None,
            author_id=None,
            guild_id=None,
            channel_id=None,
            all_locations=True,
            current_guild_id=10,
            current_channel_id=20,
        )

        self.assertEqual(channel_filters.channel_id, "40")
        self.assertIsNone(channel_filters.guild_id)
        self.assertEqual(channel_filters.author_id, "30")
        self.assertIsNone(all_filters.channel_id)
        self.assertIsNone(all_filters.guild_id)

    def test_invalid_ids_and_conflicting_location_options_are_rejected(
        self,
    ) -> None:
        common_arguments = {
            "selected_status": "UNREAD",
            "keyword": None,
            "date_from": None,
            "date_to": None,
            "author_id": None,
            "guild_id": None,
            "channel_id": None,
            "all_locations": False,
            "current_guild_id": 10,
            "current_channel_id": 20,
        }

        with self.assertRaisesRegex(ValueError, "author_id"):
            bot.create_saved_message_filters(
                **(common_arguments | {"author_id": "Alice"})
            )

        with self.assertRaisesRegex(ValueError, "cannot be combined"):
            bot.create_saved_message_filters(
                **(
                    common_arguments
                    | {"guild_id": "10", "all_locations": True}
                )
            )


class SavedFilteredCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_command_passes_same_complete_filter_to_count_and_list(
        self,
    ) -> None:
        interaction = FakeInteraction()
        row = {
            "id": 7,
            "guild_id": "10",
            "guild_name": "Guild",
            "channel_id": "20",
            "channel_name": "general",
            "author_name": "Alice",
            "content": "needle",
            "jump_url": "https://discord.test/7",
            "message_created_at": "2026-07-02T10:00:00+00:00",
            "status": "READ_KEEP",
        }

        with (
            patch.object(
                bot,
                "count_saved_messages",
                new=AsyncMock(return_value=1),
            ) as count_messages,
            patch.object(
                bot,
                "get_saved_messages",
                new=AsyncMock(return_value=[row]),
            ) as get_messages,
            patch.object(
                bot,
                "get_attachments_for_saved_messages",
                new=AsyncMock(return_value={7: []}),
            ),
        ):
            await bot.show_saved_messages.callback(
                interaction,
                status=SimpleNamespace(value="READ_KEEP"),
                keyword=" needle ",
                date_from="2026-07-01",
                date_to="2026-07-03",
                author_id="30",
                channel_id="20",
                guild_id="10",
                sort=SimpleNamespace(value="LENGTH_ASC"),
            )

        expected_filters = bot.SavedMessageFilters(
            status="READ_KEEP",
            keyword="needle",
            created_from="2026-07-01T00:00:00+00:00",
            created_before="2026-07-04T00:00:00+00:00",
            author_id="30",
            channel_id="20",
            guild_id="10",
        )
        count_filters = count_messages.await_args.kwargs["filters"]
        list_filters = get_messages.await_args.kwargs["filters"]

        self.assertEqual(count_filters, expected_filters)
        self.assertIs(count_filters, list_filters)
        self.assertEqual(
            get_messages.await_args.kwargs["sort"],
            bot.SavedItemSort.LENGTH_ASC,
        )
        content = interaction.edit_original_response.await_args.kwargs[
            "content"
        ]
        self.assertIn("Keyword: `needle`", content)
        self.assertIn("Author: <@30>", content)
        self.assertIn("Original date: `2026-07-01` to `2026-07-03`", content)
        self.assertIn("Sort: `Length ascending`", content)

    async def test_invalid_date_returns_before_database_queries(self) -> None:
        interaction = FakeInteraction()

        with (
            patch.object(
                bot,
                "count_saved_messages",
                new=AsyncMock(),
            ) as count_messages,
            patch.object(
                bot,
                "get_saved_messages",
                new=AsyncMock(),
            ) as get_messages,
        ):
            await bot.show_saved_messages.callback(
                interaction,
                date_from="tomorrow",
            )

        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        count_messages.assert_not_awaited()
        get_messages.assert_not_awaited()
        self.assertIn(
            "`date_from` must use the `YYYY-MM-DD` format.",
            interaction.edit_original_response.await_args.kwargs["content"],
        )


class SavedAutocompleteTests(unittest.IsolatedAsyncioTestCase):
    async def test_author_autocomplete_returns_named_id_choices(self) -> None:
        interaction = FakeInteraction()
        rows = [{"author_id": "30", "author_name": "Alice"}]

        with patch.object(
            bot,
            "get_saved_author_autocomplete_choices",
            new=AsyncMock(return_value=rows),
        ) as query:
            choices = await bot.saved_author_autocomplete(
                interaction,
                "ali",
            )

        query.assert_awaited_once_with(
            saved_by_user_id="42",
            current="ali",
        )
        self.assertEqual(choices[0].name, "Alice (30)")
        self.assertEqual(choices[0].value, "30")

    async def test_channel_autocomplete_searches_all_saved_locations(self) -> None:
        interaction = FakeInteraction()
        rows = [
            {
                "channel_id": "20",
                "channel_name": "general",
                "guild_id": "10",
                "guild_name": "Test Guild",
            }
        ]

        with patch.object(
            bot,
            "get_saved_channel_autocomplete_choices",
            new=AsyncMock(return_value=rows),
        ) as query:
            choices = await bot.saved_channel_autocomplete(interaction, "gen")

        query.assert_awaited_once_with(
            saved_by_user_id="42",
            current="gen",
            guild_id=None,
        )
        self.assertEqual(
            choices[0].name,
            "#general — Test Guild (20)",
        )

    async def test_channel_autocomplete_honors_selected_server_or_all(
        self,
    ) -> None:
        selected = FakeInteraction(
            namespace=SimpleNamespace(guild_id="99", all_locations=False)
        )
        all_locations = FakeInteraction(
            namespace=SimpleNamespace(all_locations=True)
        )

        with patch.object(
            bot,
            "get_saved_channel_autocomplete_choices",
            new=AsyncMock(return_value=[]),
        ) as query:
            await bot.saved_channel_autocomplete(selected, "")
            await bot.saved_channel_autocomplete(all_locations, "")

        self.assertEqual(query.await_args_list[0].kwargs["guild_id"], "99")
        self.assertIsNone(query.await_args_list[1].kwargs["guild_id"])

    async def test_guild_autocomplete_uses_fallback_name_and_safe_length(
        self,
    ) -> None:
        interaction = FakeInteraction()
        rows = [
            {"guild_id": "1" * 20, "guild_name": "G" * 100},
            {"guild_id": "10", "guild_name": None},
        ]

        with patch.object(
            bot,
            "get_saved_guild_autocomplete_choices",
            new=AsyncMock(return_value=rows),
        ):
            choices = await bot.saved_guild_autocomplete(interaction, "")

        self.assertLessEqual(len(choices[0].name), 100)
        self.assertEqual(choices[1].name, "Unknown server (10)")


if __name__ == "__main__":
    unittest.main()
