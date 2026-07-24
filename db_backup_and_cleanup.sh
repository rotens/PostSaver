#!/usr/bin/env bash

set -euo pipefail

PROJECT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
DATABASE_PATH="$PROJECT_DIRECTORY/data/reading_manager.db"
BACKUP_DIRECTORY="$PROJECT_DIRECTORY/data/backups"
REQUIRED_TABLE_COUNT=5

usage() {
    cat <<'EOF'
Usage:
  ./db_backup_and_cleanup.sh backup-clear [--yes]
  ./db_backup_and_cleanup.sh restore <backup-file> [--yes]
  ./db_backup_and_cleanup.sh list
  ./db_backup_and_cleanup.sh help

Commands:
  backup-clear  Create a verified backup, then empty every application table.
  restore       Replace the current database with a verified backup.
                A safety backup of the current database is created first.
  list          List backups created in data/backups.

Options:
  --yes         Skip the destructive-action confirmation prompt.

Stop the bot before running backup-clear or restore.
Relative backup paths are resolved from the repository root.
EOF
}

fail() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

require_sqlite3() {
    command -v sqlite3 >/dev/null 2>&1 ||
        fail "sqlite3 is required but was not found in PATH."
}

reject_unsafe_sqlite_shell_path() {
    local path=$1

    case "$path" in
        *"'"* | *$'\n'* | *$'\r'*)
            fail "Database paths containing quotes or line breaks are not supported."
            ;;
    esac
}

verify_application_database() {
    local path=$1
    local integrity_result
    local table_count

    [[ -f "$path" ]] || fail "Database file does not exist: $path"

    integrity_result="$(sqlite3 "$path" "PRAGMA integrity_check;")"
    [[ "$integrity_result" == "ok" ]] ||
        fail "SQLite integrity check failed for: $path"

    table_count="$(
        sqlite3 "$path" "
            SELECT COUNT(*)
            FROM sqlite_master
            WHERE type = 'table'
              AND name IN (
                  'saved_messages',
                  'ignored_users',
                  'pending_ranges',
                  'saved_batches',
                  'saved_batch_messages'
              );
        "
    )"

    [[ "$table_count" == "$REQUIRED_TABLE_COUNT" ]] ||
        fail "Backup does not contain all Discord Reading Manager tables: $path"
}

confirm_action() {
    local expected_text=$1
    local prompt=$2
    local skip_confirmation=$3
    local answer

    if [[ "$skip_confirmation" == "true" ]]; then
        return
    fi

    printf '%s\n' "$prompt"
    printf 'Type %s to continue: ' "$expected_text"

    if ! read -r answer; then
        fail "Confirmation input is unavailable. Use --yes only if intentional."
    fi

    if [[ "$answer" != "$expected_text" ]]; then
        printf 'Cancelled. No data was changed.\n'
        exit 0
    fi
}

create_backup() {
    local label=$1
    local timestamp
    local backup_path

    mkdir -p -- "$BACKUP_DIRECTORY"
    timestamp="$(date -u +%Y%m%d_%H%M%S)"
    backup_path="$BACKUP_DIRECTORY/reading_manager_${label}_${timestamp}_$$.db"
    reject_unsafe_sqlite_shell_path "$backup_path"

    sqlite3 "$DATABASE_PATH" ".backup '$backup_path'"
    verify_application_database "$backup_path"

    printf '%s\n' "$backup_path"
}

resolve_existing_backup_path() {
    local supplied_path=$1
    local candidate
    local directory
    local filename

    if [[ "$supplied_path" == /* ]]; then
        candidate=$supplied_path
    else
        candidate="$PROJECT_DIRECTORY/$supplied_path"
    fi

    [[ -f "$candidate" ]] || fail "Backup file does not exist: $candidate"

    directory="$(cd -- "$(dirname -- "$candidate")" && pwd -P)"
    filename="$(basename -- "$candidate")"

    printf '%s/%s\n' "$directory" "$filename"
}

show_table_counts() {
    sqlite3 -header -column "$DATABASE_PATH" <<'SQL'
SELECT 'saved_messages' AS table_name, COUNT(*) AS row_count
FROM saved_messages
UNION ALL
SELECT 'ignored_users', COUNT(*)
FROM ignored_users
UNION ALL
SELECT 'pending_ranges', COUNT(*)
FROM pending_ranges
UNION ALL
SELECT 'saved_batches', COUNT(*)
FROM saved_batches
UNION ALL
SELECT 'saved_batch_messages', COUNT(*)
FROM saved_batch_messages;
SQL
}

backup_and_clear() {
    local skip_confirmation=$1
    local backup_path
    local integrity_result

    [[ -f "$DATABASE_PATH" ]] ||
        fail "Live database does not exist: $DATABASE_PATH"
    verify_application_database "$DATABASE_PATH"

    confirm_action \
        "CLEAR" \
        "This will back up and then clear every application table. Stop the bot first." \
        "$skip_confirmation"

    backup_path="$(create_backup "before_clear")"
    printf 'Verified backup created: %s\n' "$backup_path"

    sqlite3 "$DATABASE_PATH" <<'SQL'
PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

DELETE FROM saved_batch_messages;
DELETE FROM saved_batches;
DELETE FROM saved_messages;
DELETE FROM pending_ranges;
DELETE FROM ignored_users;

DELETE FROM sqlite_sequence
WHERE name IN ('saved_messages', 'saved_batches');

COMMIT;
SQL

    integrity_result="$(sqlite3 "$DATABASE_PATH" "PRAGMA integrity_check;")"
    [[ "$integrity_result" == "ok" ]] ||
        fail "The cleared database failed its integrity check. Restore the backup."

    printf 'All application tables were cleared successfully.\n'
    show_table_counts
}

restore_backup() {
    local supplied_path=$1
    local skip_confirmation=$2
    local backup_path
    local safety_backup_path

    backup_path="$(resolve_existing_backup_path "$supplied_path")"
    reject_unsafe_sqlite_shell_path "$backup_path"
    verify_application_database "$backup_path"

    if [[ "$backup_path" == "$DATABASE_PATH" ]]; then
        fail "The restore source cannot be the live database."
    fi

    confirm_action \
        "RESTORE" \
        "This will replace the live database with the selected backup. Stop the bot first." \
        "$skip_confirmation"

    mkdir -p -- "$(dirname -- "$DATABASE_PATH")"

    if [[ -f "$DATABASE_PATH" ]]; then
        verify_application_database "$DATABASE_PATH"
        safety_backup_path="$(create_backup "before_restore")"
        printf 'Safety backup of current data created: %s\n' "$safety_backup_path"
    fi

    sqlite3 "$DATABASE_PATH" ".restore '$backup_path'"
    verify_application_database "$DATABASE_PATH"

    printf 'Database restored successfully from: %s\n' "$backup_path"
    show_table_counts
}

list_backups() {
    local backups=()

    if [[ -d "$BACKUP_DIRECTORY" ]]; then
        shopt -s nullglob
        backups=("$BACKUP_DIRECTORY"/*.db)
        shopt -u nullglob
    fi

    if (( ${#backups[@]} == 0 )); then
        printf 'No backups found in %s\n' "$BACKUP_DIRECTORY"
        return
    fi

    printf 'Backups in %s:\n' "$BACKUP_DIRECTORY"
    printf '  %s\n' "${backups[@]}"
}

parse_yes_option() {
    local option=${1:-}

    case "$option" in
        "")
            printf 'false\n'
            ;;
        --yes)
            printf 'true\n'
            ;;
        *)
            fail "Unknown option: $option"
            ;;
    esac
}

main() {
    local command=${1:-help}
    local skip_confirmation

    reject_unsafe_sqlite_shell_path "$DATABASE_PATH"

    case "$command" in
        backup-clear)
            (( $# <= 2 )) || fail "Too many arguments for backup-clear."
            require_sqlite3
            skip_confirmation="$(parse_yes_option "${2:-}")"
            backup_and_clear "$skip_confirmation"
            ;;
        restore)
            (( $# >= 2 )) || fail "restore requires a backup file."
            (( $# <= 3 )) || fail "Too many arguments for restore."
            require_sqlite3
            skip_confirmation="$(parse_yes_option "${3:-}")"
            restore_backup "$2" "$skip_confirmation"
            ;;
        list)
            (( $# == 1 )) || fail "list does not accept additional arguments."
            list_backups
            ;;
        help | --help | -h)
            usage
            ;;
        *)
            usage >&2
            fail "Unknown command: $command"
            ;;
    esac
}

main "$@"
