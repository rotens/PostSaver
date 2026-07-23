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
- `/saved` supports `UNREAD`, `READ_KEEP`, and `ALL` filters.
- Every saved-message panel provides:
  - `READ_KEEP`
  - `UNREAD`
  - `DELETE`
  - `Open message`
- Saving the same Discord message twice for the same user does not create a
  duplicate.
- Records and button actions are scoped to the user who saved them.

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
- A range can contain at most 100 messages.
- Oversized ranges are rejected without silently saving a partial range.
- Ignored authors are excluded.
- Existing saved records are associated with the batch without being
  duplicated.
- Existing `READ_KEEP` records are not reset to `UNREAD`.
- A successful operation creates one batch and ordered message associations.
- Batch creation, message insertion, association, and pending-range cleanup are
  performed in one SQLite transaction.
- The final ephemeral response reports total, newly saved, already saved, and
  ignored message counts.

The pending range start is stored in SQLite and survives bot restarts. Selecting
another start replaces the previous one.

Batch data is stored, but there is not yet a `/batches` command or other batch
browsing and management interface.

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
2. Open its **Bot** settings.
3. Enable **Message Content Intent** under **Privileged Gateway Intents**.
4. Save the change.

The intent is also enabled in `ReadingBot` in `bot.py`. Both configuration
steps are necessary for retrieving the content of messages returned by channel
history.

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

## Discord commands and interactions

### Slash commands

| Command | Purpose |
|---|---|
| `/saved` | Display the user's saved messages with status and page options. |
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
| `saved_messages` | Per-user saved Discord message records and statuses. |
| `ignored_users` | Per-user ignored-author settings. |
| `pending_ranges` | One persistent pending range start per user. |
| `saved_batches` | Optional titles and metadata for saved message batches. |
| `saved_batch_messages` | Ordered many-to-many associations between batches and saved messages. |

Discord IDs are stored as text. Duplicate saved records are prevented by:

```sql
UNIQUE(saved_by_user_id, message_id)
```

Deleting a saved message removes its batch associations through foreign-key
cascading. It does not automatically delete a batch that becomes empty.

## Tests

The tests use Python's standard `unittest` framework and temporary SQLite
databases. They do not connect to Discord.

Run the complete suite from the repository root:

```bash
python -m unittest discover -s tests -v
```

The current suite contains 67 tests covering:

- individual-message storage, duplicate handling, ordering, and pagination;
- saved-message status validation, ownership, and deletion;
- ignored-user creation, removal, reset, self-ignore, and owner isolation;
- Discord command responses and metadata passed to the database layer;
- saved-message view ownership, button states, status changes, and deletion;
- `/saved` filtering, page calculation, empty results, and panel rendering;
- pending-range creation, replacement, isolation, and deletion;
- batch creation, ownership, ordering, and associations;
- inclusive and reverse-direction history retrieval;
- the 100-message range limit;
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
    Unit tests for pending ranges, batches, and completed range saving.

requirements.txt
    Direct Python runtime dependencies.

data/reading_manager.db
    Ignored runtime database created automatically.
```

## Current limitations

- Saved batches cannot yet be listed, opened, renamed, or deleted through
  Discord.
- `/saved` cannot yet filter by author, channel, server, date, or keywords.
- Attachments and attachment-only messages are not represented beyond their
  text content.
- Messages inside a batch do not yet have a dedicated batch-detail view.
- Batch-level status changes are not implemented.
- Persistent Discord views across bot restarts are not implemented.
- The range size is currently fixed at 100 messages.

Planned work should continue in focused milestones and avoid unrelated
refactoring.
