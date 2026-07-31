# Discord Reading Manager

Discord Reading Manager is a Python Discord bot that gives each user a private
reading list of Discord messages. Users can save individual messages, organize
an inclusive range of messages as a titled batch, ignore selected authors, and
manage the reading status of saved records.

The bot uses:

- Python and `discord.py`
- asynchronous SQLite access through `aiosqlite`
- `python-dotenv` for local environment variables
- SQLite at `data/reading_manager.db`

## Current features

### Saving and viewing messages

- `Apps → Save as UNREAD` saves a selected Discord message.
- `/saved` displays saved records using five records per page.
- `/saved` supports status, literal keyword, original-message date, author,
  channel, and server filters.
- `/saved` can sort by original-message date or text length in either
  direction. Its default order remains newest save first.
- If neither a channel nor a server is supplied, `/saved` uses the channel and
  server where the command was invoked. Set `all_locations:true` to search all
  saved locations instead.
- Author, channel, and server inputs offer autocomplete choices drawn only
  from the invoking user's saved records. A Discord ID can also be entered
  directly.
- Every saved-message panel provides:
  - `READ_KEEP`
  - `UNREAD`
  - `DELETE`
  - `Open message`
- Saving the same Discord message twice for the same user does not create a
  duplicate.
- Records and button actions are scoped to the user who saved them.
- Saved-message panels list stored attachments as filename links with readable
  file sizes and preview the first image attachment.
- Saved-message panels show the stored server and channel names. Older records
  without names fall back to their Discord IDs.
- Attachment-only messages display a dedicated no-text explanation instead of
  appearing empty.

The stored statuses are:

- `UNREAD`: saved and waiting to be read.
- `READ_KEEP`: read but retained.

`READ` is not stored as a status. Permanently reading and removing a record is
represented by deleting it.

### Ignoring authors

Every user has their own ignore list. Self-ignore is supported.

Authors can be ignored or unignored with:

- `/ignore_user`
- `/unignore_user`
- `/unignore_all`
- user context-menu actions
- message-author context-menu actions

`Save as UNREAD` and message-range saving both skip messages written by ignored
authors.

### Saving message ranges

The range workflow uses two message context-menu actions:

1. Select `Apps → Set range start` on one message.
2. Select `Apps → Save through range end` on another message in the same
   channel.
3. Optionally enter a title in the modal and submit it.

Range behavior:

- Both boundary messages are included.
- Either selection direction is accepted; messages are stored oldest-first.
- Start and end must belong to the same channel.
- A selected range can span at most 1,000 Discord messages.
- Ignored authors are excluded.
- After ignored authors are excluded, at most 300 messages can be saved in one
  batch.
- Ranges exceeding either limit are rejected without silently saving a partial
  range.
- Existing saved records are associated with the batch without being
  duplicated.
- Existing `READ_KEEP` records are not reset to `UNREAD`.
- A successful operation creates one batch and ordered message associations.
- Batch creation, message insertion, association, and pending-range cleanup are
  performed in one SQLite transaction together with attachment metadata.
- The final ephemeral response reports total, newly saved, already saved, and
  ignored message counts.

The pending range start is stored in SQLite and survives bot restarts. Selecting
another start replaces the previous one.

### Viewing saved batches

`/batches page:<number>` displays five read-only batch summaries per page,
newest first. Every summary has its own `View batch` button and contains:

- the batch title, or an `Untitled batch #<id>` fallback;
- the batch creation time;
- the current number of messages;
- a preview and direct link for the first remaining or matching message;
- a direct link to the last remaining or matching message when it differs
  from the first;
- the server and channel of the previewed message;
- attachment links and the first image preview from that first message.

`View batch` opens a separate ephemeral detail response with five messages per
page. Detail pages preserve the batch order and show each message's author,
text, creation time, current status, attachments, image preview, position in
the batch, server, channel, and original-message link. `Previous` and `Next`
edit the detail response in place, and the unavailable boundary action is
disabled.

`/batches` supports the same status, original-message date, author, channel,
and server filters as `/saved`. Keywords match either batch titles or saved
message content. Filtered summaries display matching messages alongside the
batch's total message count, preview the first matching message, and preserve
the filters when `View batch`, `Previous`, or `Next` is used. With no explicit
location, the current channel and server are used. Use `all_locations:true`
with status `ALL` and no other filters for the unfiltered view, which includes
empty batches.

`/batches` can sort by batch creation date or summed message text length in
either direction. Filtered length sorting sums only matching messages;
unfiltered length sorting sums every message. Batch detail pages retain the
selected sort state but continue to display messages in their canonical batch
position order.

Empty batches remain visible, but their `View batch` button is disabled. Batch
summaries and details are currently read-only: they do not provide rename,
delete, or batch-level status controls.

### Attachments

Both individual-message and range saving preserve attachment metadata,
including the filename, Discord URLs, media type, byte size, optional alt text,
dimensions, and original order.

`/saved` lists the attachments belonging to each displayed record. `/batches`
lists attachments for the first remaining message in each summary and for
every message shown on a batch-detail page. The first image attachment is shown
as the embed image; other images and non-image files remain links. Long
attachment lists are shortened with an omitted-item count to stay within
Discord embed limits.

The bot stores metadata and URLs, not the attachment file contents. It is not
an attachment archive: a stored URL can eventually stop working if the
original Discord attachment becomes unavailable.

## Requirements

- Python 3.10 or newer; the current project has been tested with Python 3.12.3.
- A Discord application with a bot user.
- The Discord **Message Content Intent** enabled in the Developer Portal.
- Bot access to the channels being used, including permission to view channels
  and read message history.

The direct Python dependencies are declared in `requirements.txt`.

## Setup

Run setup commands from the repository root.

Create and activate a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
```

On Windows PowerShell, activate it with:

```powershell
.\.venv\Scripts\Activate.ps1
```

Install the dependencies:

```bash
python -m pip install -r requirements.txt
```

Create a local `.env` file:

```text
DISCORD_TOKEN=your_discord_bot_token
```

Do not commit `.env`. It is excluded by `.gitignore`.

In the Discord Developer Portal:

1. Open the application.
2. Open **Installation** and enable both **Guild Install** and **User
   Install** if commands should be available in servers, bot DMs, other DMs,
   and group DMs.
3. Configure the user installation with the `applications.commands` scope and
   install the application for your Discord user.
4. Open its **Bot** settings.
5. Enable **Message Content Intent** under **Privileged Gateway Intents**.
6. Save the change.

The intent is also enabled in `ReadingBot` in `bot.py`. Both configuration
steps are necessary for retrieving the content of messages returned by channel
history. The command tree explicitly supports guild installation, user
installation, server channels, direct conversations with the bot, and private
DM/group-DM command surfaces. Restart the bot after changing installation
settings so its startup `tree.sync()` updates the registered commands.

## Running the bot

From the repository root:

```bash
python bot.py
```

Running from the repository root is important because the database path is
relative:

```text
data/reading_manager.db
```

On startup, the bot:

1. creates the `data/` directory if necessary;
2. initializes the SQLite schema;
3. synchronizes the Discord application commands;
4. connects to Discord.

The `data/` directory is ignored by Git.

## Resetting test data

Stop the bot before backing up, clearing, or restoring its database. The
repository includes a helper for manual-test resets:

```bash
./db_backup_and_cleanup.sh backup-clear
```

The command creates and verifies a timestamped SQLite backup in
`data/backups/` before clearing all six application tables. It also resets
the autoincrement counters for saved messages and batches. Type `CLEAR` when
prompted, or add `--yes` to intentionally skip the prompt.

List the available backups:

```bash
./db_backup_and_cleanup.sh list
```

Restore one of them:

```bash
./db_backup_and_cleanup.sh restore data/backups/<backup-file>.db
```

Type `RESTORE` when prompted. Before replacing the live database, the script
automatically creates another backup of its current contents. Both operations
run SQLite integrity and schema checks; they leave the database structure in
place and only replace or clear its data.

## Discord commands and interactions

### Slash commands

| Command | Purpose |
|---|---|
| `/saved` | Display the user's saved messages with pagination and optional status, keyword, original-date, author, channel, and server filters. |
| `/batches` | Display and filter paginated batch summaries with read-only message details. |
| `/ignore_user` | Ignore messages written by a selected user. |
| `/unignore_user` | Remove one user from the ignore list. |
| `/unignore_all` | Reset the user's ignore list. |

### Message context-menu actions

These appear under **Apps** after right-clicking a message.

| Action | Purpose |
|---|---|
| `Save as UNREAD` | Save one selected message. |
| `Ignore message author` | Add the selected message's author to the ignore list. |
| `Unignore message author` | Remove the selected message's author from the ignore list. |
| `Set range start` | Store or replace the user's pending range start. |
| `Save through range end` | Complete an inclusive range and open the optional-title modal. |

### User context-menu actions

These appear under **Apps** when selecting a user's name.

| Action | Purpose |
|---|---|
| `Ignore user's messages` | Add the selected user to the ignore list. |
| `Unignore user's messages` | Remove the selected user from the ignore list. |

All command responses and saved-message panels are currently ephemeral.

## Database

`database.py` initializes these tables:

| Table | Purpose |
|---|---|
| `saved_messages` | Per-user saved Discord messages, statuses, and location-name snapshots. |
| `saved_message_attachments` | Ordered attachment metadata belonging to saved messages. |
| `ignored_users` | Per-user ignored-author settings. |
| `pending_ranges` | One persistent pending range start per user. |
| `saved_batches` | Optional titles and metadata for saved message batches. |
| `saved_batch_messages` | Ordered many-to-many associations between batches and saved messages. |

Discord IDs are stored as text. Duplicate saved records are prevented by:

```sql
UNIQUE(saved_by_user_id, message_id)
```

New saved records store nullable `guild_name` and `channel_name` snapshots in
addition to their stable Discord IDs. On startup, existing databases are
upgraded additively if either column is missing. Existing records are
preserved; their new name columns remain `NULL` and displays fall back to IDs.

The saved-message count and listing queries share one `SavedMessageFilters`
value. The database layer supports status, literal case-insensitive content
keywords, original-message date boundaries, author ID, channel ID, and server
ID. `created_from` is inclusive and `created_before` is exclusive. `/saved`
accepts dates as `YYYY-MM-DD`; its user-facing end date includes that entire
UTC calendar day and is converted to the exclusive start of the next day for
the database query.

Autocomplete queries shared by `/saved` and `/batches` are scoped to the user
who invokes the command, return at most 25 choices, and match stored names or
Discord IDs. When a server is explicitly selected, channel autocomplete is
limited to that server. Without an explicit server, channel autocomplete
searches every saved server and DM so a private channel can be selected while
the command is invoked from a server.

Batch summary queries return total and matching message counts and select the
lowest- and highest-position matching messages for the first and last links.
They also return total and matching text-length aggregates. Filtered
batch-detail count and listing queries use the same `SavedMessageFilters`
value. Keyword matching checks both the batch title and message content; all
other filters still apply to individual messages. All summary and detail
queries remain owner-scoped.

`SavedItemSort` validates the shared `DEFAULT`, `DATE_DESC`, `DATE_ASC`,
`LENGTH_DESC`, and `LENGTH_ASC` modes before a fixed SQL ordering is selected.
Every ordering includes the record ID as a deterministic tie-breaker so page
boundaries remain stable when dates or lengths are equal. Text length is the
number of characters in `content`; attachment-only messages therefore have
length zero.

Deleting a saved message removes its attachment metadata and batch
associations through foreign-key cascading. It does not automatically delete
a batch that becomes empty.

## Tests

The tests use Python's standard `unittest` framework and temporary SQLite
databases. They do not connect to Discord.

Run the complete suite from the repository root:

```bash
python -m unittest discover -s tests -v
```

The current suite contains 145 tests covering:

- individual-message storage, duplicate handling, deterministic date/length
  sorting, ordering, and pagination;
- attachment schema, metadata conversion, ordering, validation, ownership,
  single-message and range capture, transaction rollback, cascade deletion,
  command rendering, image previews, and display truncation;
- saved-message status validation, ownership, and deletion;
- ignored-user creation, removal, reset, self-ignore, and owner isolation;
- Discord command responses and metadata passed to the database layer;
- additive location-column migration, single-message and range location
  capture, query propagation, readable location rendering, and old-record
  fallbacks;
- saved-message view ownership, button states, status changes, and deletion;
- `/saved` filter parsing and validation, current-location defaults, active
  filter summaries, page calculation, empty results, and panel rendering;
- author, channel, and server autocomplete ownership, matching, location
  scoping, and Discord choice rendering;
- individual and combined saved-message query filters, literal keyword
  escaping, original-message date boundaries, and count/list consistency;
- pending-range creation, replacement, isolation, and deletion;
- batch creation, ownership, ordering, associations, filtered total/matching
  counts and text lengths, first/last matching links, title searches,
  deterministic date/length sorting, and paginated contents;
- `/batches` filter parsing and autocomplete reuse, active-filter summaries,
  empty states, page validation, per-summary views, filtered detail opening,
  filter-preserving navigation, ownership, attachment rendering, and embed
  limits;
- inclusive and reverse-direction history retrieval;
- the 1,000-message scan limit and 300-message saved-message limit;
- ignored-author filtering;
- duplicate and `READ_KEEP` handling;
- atomic range saving and rollback;
- stale range protection, validation failures, and pending-range cleanup;
- optional-title and modal response behavior.

Manual Discord testing has also confirmed the current range-saving workflow.

## Repository structure

```text
bot.py
    Discord client, commands, context menus, views, modal, and range workflow.

database.py
    SQLite schema and asynchronous database functions.

tests/
    Unit tests for saved messages, attachments, pending ranges, batches, and
    completed range saving.

requirements.txt
    Direct Python runtime dependencies.

data/reading_manager.db
    Ignored runtime database created automatically.
```

## Current limitations

- Saved batches cannot yet be renamed or deleted through Discord.
- Attachment files are not downloaded or archived; views depend on stored
  Discord URLs remaining available.
- Batch-level status changes are not implemented.
- Persistent Discord views across bot restarts are not implemented.
- A selected range can currently span at most 1,000 Discord messages, and each
  completed range can save at most 300 non-ignored messages.

Planned work should continue in focused milestones and avoid unrelated
refactoring.
