import json
import logging
import os
import secrets
import sqlite3
import stat
from contextlib import closing
from datetime import UTC, datetime, timedelta

from audible_sync.model import Account, Book, BookStatus, RunBook, RunBookOutcome, SyncOutcome, SyncRun
from audible_sync.paths import REPO_ROOT
from audible_sync.security import crypto

logger = logging.getLogger(__name__)

DB_FILE = str(REPO_ROOT / "data" / "audible_sync.db")

# A download can outlive its process: a container restart or a kill -9 leaves the row in
# `downloading` with nothing working on it. Six hours is comfortably longer than the
# slowest real book (a licence, several gigabytes over a CDN, then an Opus re-encode) and
# short enough that a crashed run recovers on the next scheduler tick rather than by hand.
STALE_CLAIM_SECONDS = 6 * 60 * 60

# Whether a `downloading` row counts as abandoned: neither the claim nor its last renewal
# (`renew_claim`) is newer than the cutoff bound to the `?`. A NULL counts as abandoned,
# or such a row would never be picked up by anything again. Shared by the queue and the
# claim, which must agree on it.
_ABANDONED = "(COALESCE(claim_renewed_at, last_attempt_at) IS NULL OR COALESCE(claim_renewed_at, last_attempt_at) < ?)"


def _get_connection() -> sqlite3.Connection:
    """
    Connection whose rows come back as `sqlite3.Row`, so reads can be mapped by
    column name.

    `_migrate_schema` appends columns to an older database in whatever order it
    finds them missing, so the position of a column in `SELECT *` was never a
    reliable thing to index.
    """
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = _get_connection()
    cursor = conn.cursor()

    # A reader no longer blocks the writer, which matters now a scheduler tick can run
    # while another process is downloading. The mode is a property of the database file
    # rather than of the connection, so setting it once here holds for every later
    # connection - including ones opened by a process that never calls init_db.
    cursor.execute("PRAGMA journal_mode=WAL")

    # The file holds credentials (encrypted, but still) and the API token: owner-only.
    # Best effort - a bind mount or an odd file system may refuse, and that is not fatal.
    # SQLite gives the -wal and -shm files the mode of the database file.
    try:
        os.chmod(DB_FILE, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        logger.debug("Could not restrict the permissions of %s", DB_FILE, exc_info=True)

    # One row per marketplace login. `auth` is the Authenticator blob as JSON, NULL for
    # an account that has no working credentials yet (see `Account`).
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            country_code TEXT NOT NULL DEFAULT '',
            customer_name TEXT,
            -- Amazon's id for the customer (`customer_info.user_id`), outside the
            -- encrypted blob so the database can hold one login to one account
            customer_id TEXT,
            auth JSON,
            enabled BOOLEAN NOT NULL DEFAULT 1,
            monitor_existing BOOLEAN NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            last_synced_at TEXT
        )
    """)

    # A book is one ASIN in one account's library. The same ASIN can be owned in two
    # marketplaces, so the identity is the pair and `id` is what the API addresses.
    cursor.execute(_library_ddl())

    # One row per pipeline run of one account, covering both halves of it: the library
    # sync and the downloads that followed. The newest start time of a run whose sync
    # completed is that account's next incremental cursor, which the library table
    # could never provide - `MAX(date_added)` says what was purchased, not when we
    # last looked. `account_id` is NULL on rows from before there were accounts.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sync_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER REFERENCES accounts(id),
            started_at TEXT NOT NULL,
            -- NULL while the run is in flight. A row still `running` long after its
            -- started_at is a run that was killed, which is the state the todo item
            -- existed to make visible.
            finished_at TEXT,
            outcome TEXT NOT NULL,
            books_seen INTEGER NOT NULL DEFAULT 0,
            books_added INTEGER NOT NULL DEFAULT 0,
            books_downloaded INTEGER NOT NULL DEFAULT 0,
            books_failed INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            -- 1 on runs that record their books in sync_run_books; 0 on runs from
            -- before there was that table, so "no books" and "not recorded" differ
            books_recorded BOOLEAN NOT NULL DEFAULT 0,
            -- 1 on a run that read the account's whole library rather than what was
            -- purchased since the last run: a full refresh, asked for or due, or the
            -- account's first sync
            full_refresh BOOLEAN NOT NULL DEFAULT 0
        )
    """)
    # One row per book a run took on and finished with: downloaded, failed, or refused
    # a licence. `asin` and `title` are a snapshot so the record still reads after the
    # book's row is gone. SQLite foreign keys are not switched on here, so
    # `delete_account` removes these rows itself.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sync_run_books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES sync_runs(id) ON DELETE CASCADE,
            book_id INTEGER REFERENCES library(id) ON DELETE SET NULL,
            asin TEXT NOT NULL,
            title TEXT,
            outcome TEXT NOT NULL,
            error TEXT,
            bytes INTEGER,
            seconds REAL,
            finished_at TEXT NOT NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sync_run_books_run ON sync_run_books(run_id)")
    # Runtime settings as text, one row per key. Seeded from config.ini on the first run
    # and changed through the API after that; `audible_sync.settings` owns the parsing.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    # Web app sign-ins. `id` is the SHA-256 of the cookie's token, never the token, so
    # a copy of the database cannot be used to take over a session (`audible_sync.security.webauth`).
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            persistent BOOLEAN NOT NULL,
            created_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
    """)
    conn.commit()

    # Migrate existing databases to add new columns
    _migrate_schema(conn)
    # ... and to the per-account shape, which needs the table rebuilt
    _migrate_library_to_accounts(conn)

    _create_indexes(conn)

    conn.close()


def _create_indexes(conn: sqlite3.Connection) -> None:
    """
    Index the columns every run filters or sorts on.

    Sync reads the newest `date_added`; the download queue selects on `status` and
    orders by `date_added`, so that one is composite and the ordering comes from the
    index rather than from sorting every waiting row. SQLite can serve a plain `status`
    lookup from the composite's leading column too, which makes the old single-column
    `idx_library_status` redundant - it is dropped rather than left alongside.

    `sync_runs` gets the shape of the cursor query: filter on `outcome`, take the
    newest `started_at`.

    Guarded by the columns actually present so an older database that predates a column
    is still upgradable: the composite raises on a schema with no `date_added`.
    """
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(library)")
    existing_columns = {row[1] for row in cursor.fetchall()}

    cursor.execute("PRAGMA table_info(sync_runs)")
    run_columns = {row[1] for row in cursor.fetchall()}
    if run_columns:
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sync_runs_outcome_started_at ON sync_runs(outcome, started_at)")
    if "account_id" in run_columns:
        # The cursor is per account now: filter on account and outcome, take the newest start
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_sync_runs_account_outcome_started_at "
            "ON sync_runs(account_id, outcome, started_at)"
        )

    # One Audible login, one account: the same customer in the same marketplace. A
    # second row would hold the same library and download every book again. Partial,
    # since an account whose login does not say whose it is (or has none yet) is NULL.
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_customer "
        "ON accounts(customer_id, lower(country_code)) WHERE customer_id IS NOT NULL"
    )

    if "date_added" in existing_columns:
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_library_date_added ON library(date_added)")

    if {"status", "date_added"} <= existing_columns:
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_library_status_date_added ON library(status, date_added)")
        cursor.execute("DROP INDEX IF EXISTS idx_library_status")
    elif "status" in existing_columns:
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_library_status ON library(status)")

    conn.commit()


def _migrate_schema(conn):
    """Add new columns to existing databases if they don't exist"""
    cursor = conn.cursor()

    # Get existing columns
    cursor.execute("PRAGMA table_info(library)")
    existing_columns = {row[1] for row in cursor.fetchall()}

    # Add missing columns
    new_columns = {
        "pdf_path": "TEXT",
        "cover_path": "TEXT",
        "annotations_path": "TEXT",
        "has_pdf": "BOOLEAN DEFAULT 0",
        "encoding_format": "TEXT",
        "downloaded_at": "TEXT",
        "is_consumable": "BOOLEAN",
        "attempts": "INTEGER NOT NULL DEFAULT 0",
        "last_error": "TEXT",
        "last_attempt_at": "TEXT",
        "monitored": "BOOLEAN NOT NULL DEFAULT 1",
        "file_path": "TEXT",
        "claim_token": "TEXT",
        "description": "TEXT",
        "claim_renewed_at": "TEXT",
    }

    for column, column_type in new_columns.items():
        if column not in existing_columns:
            cursor.execute(f"ALTER TABLE library ADD COLUMN {column} {column_type}")

    cursor.execute("PRAGMA table_info(accounts)")
    account_columns = {row[1] for row in cursor.fetchall()}
    if account_columns and "customer_id" not in account_columns:
        # Filled in at start by `backfill_customer_ids`, which needs the key
        cursor.execute("ALTER TABLE accounts ADD COLUMN customer_id TEXT")

    cursor.execute("PRAGMA table_info(sync_runs)")
    run_columns = {row[1] for row in cursor.fetchall()}
    if "account_id" not in run_columns:
        cursor.execute("ALTER TABLE sync_runs ADD COLUMN account_id INTEGER REFERENCES accounts(id)")
    if "books_recorded" not in run_columns:
        # Every existing run predates the per-book records, hence the default 0
        cursor.execute("ALTER TABLE sync_runs ADD COLUMN books_recorded BOOLEAN NOT NULL DEFAULT 0")
    if "full_refresh" not in run_columns:
        # Not known for the runs before; none of them counts as one, so the first run
        # after the upgrade is a full refresh while the daily one is on
        cursor.execute("ALTER TABLE sync_runs ADD COLUMN full_refresh BOOLEAN NOT NULL DEFAULT 0")

    conn.commit()


# The columns a rebuilt `library` carries, in DDL order; `_migrate_library_to_accounts`
# copies whichever of them the old table has.
_LIBRARY_COLUMNS = (
    "asin", "title", "subtitle", "authors", "narrators", "series", "genres", "length",
    "is_finished", "percent_complete", "date_added", "release_date", "cover_url", "status",
    "monitored", "file_path", "pdf_path", "cover_path", "annotations_path", "has_pdf", "encoding_format",
    "downloaded_at", "is_consumable", "attempts", "last_error", "last_attempt_at", "claim_token",
    "description", "claim_renewed_at",
)  # fmt: skip


def _migrate_library_to_accounts(conn: sqlite3.Connection) -> None:
    """
    Rebuild a `library` written before there were accounts.

    The old table was keyed by `asin` alone; the new one has a surrogate `id` and is
    unique on `(account_id, asin)`. SQLite cannot change a primary key in place, so the
    rows are copied into a fresh table under a single account. That account is created
    here with no credentials if none exists, and reads as needing a login rather than
    blocking the start; a login with its `account_id` (`accounts.replace_account_auth`)
    fills it in under the same id, so the books stay attached.
    """
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(library)")
    old_columns = [row[1] for row in cursor.fetchall()]
    if not old_columns or "account_id" in old_columns:
        return

    cursor.execute("SELECT id FROM accounts ORDER BY id LIMIT 1")
    row = cursor.fetchone()
    if row is None:
        cursor.execute(
            "INSERT INTO accounts (name, country_code, auth, created_at) VALUES (?, '', NULL, ?)",
            (LEGACY_ACCOUNT_NAME, _utcnow()),
        )
        account_id = cursor.lastrowid
    else:
        account_id = row[0]

    copied = [column for column in _LIBRARY_COLUMNS if column in old_columns]
    columns = ", ".join(copied)
    cursor.execute("ALTER TABLE library RENAME TO library_legacy")
    # The CREATE TABLE in init_db is IF NOT EXISTS, and the renamed table no longer
    # answers to `library`, so running init_db's DDL again creates the new shape
    cursor.execute(_library_ddl())
    cursor.execute(
        f"INSERT INTO library (account_id, {columns}) SELECT ?, {columns} FROM library_legacy", (account_id,)
    )
    cursor.execute("DROP TABLE library_legacy")
    # The runs recorded before there were accounts were this library's, and are its
    # sync cursor: they belong to this account and to no other
    cursor.execute("UPDATE sync_runs SET account_id = ? WHERE account_id IS NULL", (account_id,))
    # Indexes went with the old table; `_create_indexes` recreates them
    conn.commit()


# What the migration names the account it has to invent for a pre-accounts library
LEGACY_ACCOUNT_NAME = "Audible"


def _library_ddl() -> str:
    """The `library` DDL, shared by `init_db` and the rebuild so the two cannot drift."""
    return """
        CREATE TABLE IF NOT EXISTS library (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL REFERENCES accounts(id),
            asin TEXT NOT NULL,
            title TEXT,
            subtitle TEXT,
            authors JSON,
            narrators JSON,
            series JSON,
            genres JSON,
            length INTEGER,
            is_finished BOOLEAN,
            percent_complete REAL,
            date_added TEXT,
            release_date TEXT,
            cover_url TEXT,
            status TEXT,
            -- Whether the book is wanted at all; an unmonitored book is never queued
            monitored BOOLEAN NOT NULL DEFAULT 1,
            -- Where the finished audio was filed; the accessories follow
            file_path TEXT,
            pdf_path TEXT,
            cover_path TEXT,
            annotations_path TEXT,
            has_pdf BOOLEAN DEFAULT 0,
            encoding_format TEXT,
            downloaded_at TEXT,
            -- Nullable on purpose: NULL means a row written before this column
            -- existed, i.e. consumability has never been read from the API for it.
            is_consumable BOOLEAN,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            last_attempt_at TEXT,
            -- The claim a run holds while `downloading`, which every outcome must present
            -- (`claim_book_for_download`); NULL otherwise
            claim_token TEXT,
            -- When the run holding the claim last said it is still working on the book
            -- (`renew_claim`); NULL until it first does
            claim_renewed_at TEXT,
            -- The publisher's summary, HTML as Audible gives it; NULL until a sync reads it
            description TEXT,
            UNIQUE (account_id, asin)
        )
    """


def update_books(account_id: int, books: list[Book], *, monitor_new: bool = True) -> int:
    """
    Insert new books into one account's library, refresh the mutable API fields on the
    ones already stored, and return how many rows were genuinely added.

    `(account_id, asin)` is unique, so one upsert does the de-duplication. Checking first
    on a second connection could not see the rows this transaction had already inserted,
    so a repeated ASIN inside a single API response raised IntegrityError and rolled the
    whole sync back.

    `monitor_new` is what a book inserted by this call gets for `monitored`; it is never
    written on conflict, because whether a book already in the library is wanted is the
    user's decision, not the sync's.

    The conflict clause deliberately refreshes only what Audible owns. It never writes
    `date_added` (the incremental sync cursor - moving it would skip or re-fetch
    purchases), and never `status`, `attempts`, `last_error`, `last_attempt_at`, the
    three accessory paths, `encoding_format` or `downloaded_at`, all of which belong to
    the downloader. Before this it was an INSERT OR IGNORE, so `is_finished` and
    `percent_complete` stayed frozen at whatever they were the day a book was first seen.

    The one exception is `status`, and only between `waiting_download` and `unavailable`.
    Audible withdraws Plus titles the customer still holds and later offers them again,
    so consumability is the sync's business: a withdrawn book leaves the queue, and a
    restored one rejoins it without anybody having to do anything.
    """
    # Decoded again by `Book.from_row`; keep the two sides in step.
    rows = [
        (
            account_id,
            book.asin,
            book.title,
            book.subtitle,
            json.dumps(book.authors),
            json.dumps(book.narrators),
            json.dumps(book.series),
            json.dumps(book.genres),
            book.length,
            book.is_finished,
            book.percent_complete,
            book.date_added,
            book.release_date,
            book.cover_url,
            # Insert only; the conflict clause below decides an existing book's status.
            # A title Audible has withdrawn goes straight to `unavailable` so it never
            # enters the queue and never costs a licence request.
            BookStatus.WAITING_DOWNLOAD if book.is_consumable else BookStatus.UNAVAILABLE,
            monitor_new,
            book.has_pdf,
            book.is_consumable,
            book.description,
            # Bound for the CASE in the conflict clause below
            BookStatus.WAITING_DOWNLOAD,
            BookStatus.UNAVAILABLE,
            BookStatus.UNAVAILABLE,
            BookStatus.WAITING_DOWNLOAD,
        )
        for book in books
    ]

    with closing(_get_connection()) as conn:
        cursor = conn.cursor()
        # `cursor.rowcount` after an `executemany` of an upsert is -1, not a count, and
        # `RETURNING` cannot be used with `executemany` at all. Counting either side of
        # the write on the same cursor is the one thing that stays honest, and both reads
        # sit inside the same transaction so no other writer can slip between them.
        books_before = cursor.execute("SELECT COUNT(*) FROM library").fetchone()[0]
        cursor.executemany(
            """
            INSERT INTO library (account_id, asin, title, subtitle, authors, narrators, series, genres,
                                 length, is_finished, percent_complete, date_added, release_date,
                                 cover_url, status, monitored, has_pdf, is_consumable, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, asin) DO UPDATE SET
                title = excluded.title,
                subtitle = excluded.subtitle,
                authors = excluded.authors,
                narrators = excluded.narrators,
                series = excluded.series,
                genres = excluded.genres,
                length = excluded.length,
                is_finished = excluded.is_finished,
                percent_complete = excluded.percent_complete,
                release_date = excluded.release_date,
                cover_url = excluded.cover_url,
                has_pdf = excluded.has_pdf,
                is_consumable = excluded.is_consumable,
                -- Publishers edit them, so every read refreshes it; a response without
                -- the field leaves the one stored rather than forgetting it
                description = COALESCE(excluded.description, library.description),
                -- The only status the sync may change, in either direction. A book
                -- Audible has withdrawn leaves the queue; one it has offered again
                -- rejoins it, which is how a restored Plus title comes back on its own.
                -- "Offered again" means the API's view flipped: the stored row said
                -- withdrawn and this response says consumable. A book parked because a
                -- licence was refused while the API said consumable all along is not
                -- restored here - it would be refused again, and with a download cap it
                -- would take a slot from a book that can download. `retry` is for that.
                -- Every other state - downloading, downloaded, failed - is left alone.
                status = CASE
                    WHEN excluded.is_consumable = 0 AND library.status = ? THEN ?
                    WHEN excluded.is_consumable = 1 AND library.is_consumable = 0 AND library.status = ? THEN ?
                    ELSE library.status
                END
            """,
            rows,
        )
        books_added = cursor.execute("SELECT COUNT(*) FROM library").fetchone()[0] - books_before
        conn.commit()

    return books_added


def get_books(limit: int | None = None, *, account_id: int | None = None) -> list[Book]:
    """All books, newest `date_added` first; one account's or everyone's."""
    sql = "SELECT * FROM library"
    params: tuple = ()
    if account_id is not None:
        sql = f"{sql} WHERE account_id = ?"
        params = (account_id,)
    sql = f"{sql} ORDER BY date_added DESC"
    if limit is not None:
        sql = f"{sql} LIMIT ?"
        params = (*params, limit)

    with closing(_get_connection()) as conn:
        return [Book.from_row(row) for row in conn.execute(sql, params)]


def get_books_to_download(*, account_id: int | None = None, stale_after: int = STALE_CLAIM_SECONDS) -> list[Book]:
    """
    Books the downloader should try, oldest first: one account's, or everyone's.

    Only monitored books: an unmonitored one is not wanted, whatever its status says.

    Both the rows still `waiting_download` and any left in `downloading` by a process
    that died. Selecting only `waiting_download` made `STALE_CLAIM_SECONDS` unreachable:
    `claim_book_for_download` has always been able to reclaim an abandoned row, but
    nothing ever offered it one, so a book a killed run had claimed stayed `downloading`
    forever, its part-file with it. It uses the same `stale_after` and the same
    NULL-counts-as-stale rule as the claim, so the query and that UPDATE cannot disagree
    about what "abandoned" means.

    This still does **not** claim anything - the caller claims each book individually, so
    a row a live run holds is offered here and then simply fails to claim. That is the
    same race the claim already settles, and the reason this can afford to be generous.

    The claim times are not in the `(status, date_added)` index, but the `downloading`
    rows are a handful at most, so the extra test costs nothing.

    Args:
        stale_after: Seconds after which a `downloading` row is treated as abandoned

    Returns:
        Books to attempt, oldest `date_added` first
    """
    sql = f"""
        SELECT * FROM library
         WHERE monitored = 1
           AND (status = ? OR (status = ? AND {_ABANDONED}))
    """
    params: tuple = (BookStatus.WAITING_DOWNLOAD, BookStatus.DOWNLOADING, _stale_cutoff(stale_after))
    if account_id is not None:
        sql = f"{sql} AND account_id = ?"
        params = (*params, account_id)
    with closing(_get_connection()) as conn:
        rows = conn.execute(f"{sql} ORDER BY date_added ASC", params)
        return [Book.from_row(row) for row in rows]


def get_book(book_id: int) -> Book | None:
    """One book by its row id, which is how the API addresses one."""
    with closing(_get_connection()) as conn:
        row = conn.execute("SELECT * FROM library WHERE id = ?", (book_id,)).fetchone()
    return Book.from_row(row) if row is not None else None


def get_book_by_asin(asin: str, *, account_id: int | None = None) -> Book | None:
    """
    One book by ASIN: in one account's library, or the first found in any.

    Without an account this is only unambiguous while one account holds the ASIN, so
    anything addressing a specific book should use `get_book`.
    """
    sql = "SELECT * FROM library WHERE asin = ?"
    params: tuple = (asin,)
    if account_id is not None:
        sql = f"{sql} AND account_id = ?"
        params = (asin, account_id)
    with closing(_get_connection()) as conn:
        row = conn.execute(f"{sql} ORDER BY id LIMIT 1", params).fetchone()
    return Book.from_row(row) if row is not None else None


def latest_date_added(account_id: int) -> str | None:
    """
    The newest `date_added` in one account's library, or None when it is empty.

    Sync uses this as its incremental cursor. Reading it directly keeps that
    cursor independent of how `get_books` happens to sort or paginate.
    """
    with closing(_get_connection()) as conn:
        return conn.execute("SELECT MAX(date_added) FROM library WHERE account_id = ?", (account_id,)).fetchone()[0]


def needs_consumability_refresh(account_id: int) -> bool:
    """
    Whether one account's library holds books whose availability the incremental sync
    cannot see.

    The incremental sync only fetches what was purchased after the newest `date_added`,
    so a book already in the library is never re-read and its `is_consumable` never
    changes. That matters in both directions: a title Audible withdrew and has offered
    again would stay parked forever, and a row written before the column existed has
    never had its availability read at all.

    True when either is present, which is the signal for `sync_library` to re-read the
    whole library once. It keys on the stored `is_consumable`, not on `status`: a book
    parked because a licence was refused still reads consumable, a re-read would only
    confirm that, and `update_books` would not restore it anyway.

    A withdrawn book only counts while it is parked: one already downloaded, or one
    the user has since retried, has nothing waiting on the re-read, and counting it kept
    the full re-read on for good once any downloaded title had been withdrawn.
    """
    with closing(_get_connection()) as conn:
        row = conn.execute(
            """
            SELECT EXISTS(
                SELECT 1 FROM library
                 WHERE account_id = ?
                   AND (is_consumable IS NULL OR (is_consumable = 0 AND status = ?))
            )
            """,
            (account_id, BookStatus.UNAVAILABLE),
        ).fetchone()
    return bool(row[0])


def _utcnow() -> str:
    """Current UTC time as an ISO 8601 string with second precision, e.g. 2026-09-08T05:16:15+00:00."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _stale_cutoff(seconds: int) -> str:
    """
    The timestamp `seconds` in the past, formatted exactly as `_utcnow` writes them.

    Every stored timestamp carries the same `+00:00` offset and second precision, so
    "older than" is a plain string comparison in SQL and no column has to be parsed.

    Deliberately its own function rather than an argument on `_utcnow`, whose
    zero-argument signature the tests monkeypatch.
    """
    return (datetime.now(UTC) - timedelta(seconds=seconds)).replace(microsecond=0).isoformat()


def claim_book_for_download(book_id: int, *, stale_after: int = STALE_CLAIM_SECONDS) -> str | None:
    """
    Take ownership of one book, moving it to `downloading` and counting the attempt.

    The whole decision is a single UPDATE, so two processes - a scheduler tick starting
    while a manual run is still going - cannot both take the same book: the second
    matches no rows, because the first has already moved it out of `waiting_download`.

    A row abandoned in `downloading` by a process that died is claimable again once both
    its claim (`last_attempt_at`) and the claim's last renewal (`renew_claim`) are older
    than `stale_after`. A NULL timestamp counts as stale too, since such a row would
    otherwise never be picked up by anything again. A run renews its claim while it
    works, so a book that takes longer than `stale_after` is not taken over under it.

    The claim is a fresh random token, stored on the row and handed back, which every
    outcome (`mark_book_downloaded`, `mark_book_failed`, `mark_book_unavailable`,
    `release_book`) must present. "Abandoned" is only a guess from the clock: a run
    still alive past `stale_after` (a CLI run beside the service, a very slow book)
    loses the book to the reclaim, and without the token the two runs would overwrite
    each other's outcome - a failure recorded over a finished download, say. With it,
    only the newest claim can record anything.

    Args:
        book_id: The book to claim
        stale_after: Seconds after which a `downloading` row is treated as abandoned

    Returns:
        The claim if this caller now owns the book, None if somebody else does
    """
    token = secrets.token_hex(16)
    with closing(_get_connection()) as conn:
        cursor = conn.execute(
            f"""
            UPDATE library
               SET status = ?,
                   attempts = attempts + 1,
                   last_attempt_at = ?,
                   claim_token = ?,
                   claim_renewed_at = NULL
             WHERE id = ?
               AND (status = ? OR (status = ? AND {_ABANDONED}))
            """,
            (
                BookStatus.DOWNLOADING,
                _utcnow(),
                token,
                book_id,
                BookStatus.WAITING_DOWNLOAD,
                BookStatus.DOWNLOADING,
                _stale_cutoff(stale_after),
            ),
        )
        conn.commit()

    return token if cursor.rowcount == 1 else None


# Every outcome's WHERE: the row is still this claim's. `claim_token` is cleared with
# the outcome, so a claim records one outcome at most.
_HELD = f"id = ? AND status = '{BookStatus.DOWNLOADING}' AND claim_token = ?"


def renew_claim(book_id: int, *, claim: str) -> bool:
    """
    Say that the run holding this claim is still working on the book, so it is not
    taken for abandoned (see `claim_book_for_download`).

    Only the holder can renew: False once the claim has been recorded, released or
    taken over, which tells the caller to stop renewing.
    """
    with closing(_get_connection()) as conn:
        cursor = conn.execute(
            f"UPDATE library SET claim_renewed_at = ? WHERE {_HELD}",
            (_utcnow(), book_id, claim),
        )
        conn.commit()
    return cursor.rowcount == 1


def mark_book_failed(
    book_id: int, error: str, *, claim: str, max_attempts: int, terminal: bool = False
) -> BookStatus | None:
    """
    Record why a download failed and decide whether the book gets another go.

    The retry decision is made inside the UPDATE, from the `attempts` the claim has
    already incremented, so it cannot race another process reading the row between a
    SELECT and an UPDATE. A book that has used up `max_attempts` becomes `failed` and is
    never selected again; before this, a book that could not possibly succeed was
    re-licensed, re-downloaded and re-failed on every run forever.

    `terminal` short-circuits that count for a failure already known to be permanent: a
    licence Audible refuses is not going to be granted on the third ask.

    Args:
        book_id: The book that failed
        error: Message stored in `last_error` for a later run, or a UI, to show
        claim: What `claim_book_for_download` returned
        max_attempts: Attempts allowed before the book is given up on
        terminal: Fail the book now, whatever `attempts` says

    Returns:
        The status the book ended up in, or None if there is no such book or the claim
        is no longer held
    """
    with closing(_get_connection()) as conn:
        row = conn.execute(
            f"""
            UPDATE library
               SET status = CASE WHEN ? OR attempts >= ? THEN ? ELSE ? END,
                   last_error = ?,
                   claim_token = NULL
             WHERE {_HELD}
         RETURNING status
            """,
            (terminal, max_attempts, BookStatus.FAILED, BookStatus.WAITING_DOWNLOAD, error, book_id, claim),
        ).fetchone()
        conn.commit()

    return None if row is None else BookStatus(row["status"])


def mark_book_unavailable(book_id: int, error: str, *, claim: str) -> bool:
    """
    Park a book Audible will not license, without failing it.

    Deliberately not terminal, and it does not count towards `max_attempts`: the book
    leaves the queue so it stops costing a licence request (and a `max_download` slot)
    every run, with the reason in `last_error`, until somebody asks for it again with
    `reset_for_download` (the API's `retry`).

    `is_consumable` is left as the sync read it. It is the API's view, and for a book
    that reaches here the API said consumable - Audible refused the download licence
    anyway (a title retired to streaming only, say). Writing 0 here used to make the
    next sync's availability refresh "restore" the book, which queued it to be refused
    again on every run; with a download cap, two such books blocked everything else.
    A title the API itself reports withdrawn never gets here: the sync parks it.

    `attempts` is left where the claim put it: the attempt really did happen, and
    nothing terminal is decided from it while the book sits here.

    Returns whether the claim was still held, and so whether the book was parked.
    """
    with closing(_get_connection()) as conn:
        cursor = conn.execute(
            f"""
            UPDATE library
               SET status = ?,
                   last_error = ?,
                   claim_token = NULL
             WHERE {_HELD}
            """,
            (BookStatus.UNAVAILABLE, error, book_id, claim),
        )
        conn.commit()
    return cursor.rowcount == 1


def release_book(book_id: int, *, claim: str) -> bool:
    """
    Put a claimed book back exactly as it was found, attempt count and all.

    Used when a run is abandoned for a reason that has nothing to do with the book:
    expired credentials fail every book equally, so charging that to whichever book
    happened to be next would eventually mark a perfectly good one `failed`.

    `last_attempt_at` is deliberately left where the claim put it - it is a record of
    when the book was last touched, and nothing decides anything from it once the status
    is back to `waiting_download`.

    Returns whether the claim was still held, and so whether the book went back.
    """
    with closing(_get_connection()) as conn:
        cursor = conn.execute(
            f"""
            UPDATE library
               SET status = ?,
                   attempts = MAX(attempts - 1, 0),
                   claim_token = NULL
             WHERE {_HELD}
            """,
            (BookStatus.WAITING_DOWNLOAD, book_id, claim),
        )
        conn.commit()
    return cursor.rowcount == 1


def mark_book_downloaded(
    book_id: int,
    encoding_format: str | None = None,
    *,
    claim: str,
    file_path: str | None = None,
    pdf_path: str | None = None,
    cover_path: str | None = None,
    annotations_path: str | None = None,
) -> None:
    """
    Set the book to downloaded and record when, in which format, and where it and its
    accessories were filed.

    The accessory paths are written in the same statement as the status so a book
    cannot end up with its paths recorded but its status left behind (or the
    reverse) if the process stops between two commits.

    `last_error` is cleared here: a book that succeeded on its second attempt must not
    keep showing the first attempt's failure. `attempts` is kept on purpose - it is a
    true record of what the book cost.

    Returns whether the claim was still held, and so whether anything was recorded.
    """
    with closing(_get_connection()) as conn:
        cursor = conn.execute(
            f"""
            UPDATE library
               SET status = ?,
                   last_error = NULL,
                   claim_token = NULL,
                   encoding_format = ?,
                   downloaded_at = ?,
                   file_path = COALESCE(?, file_path),
                   pdf_path = COALESCE(?, pdf_path),
                   cover_path = COALESCE(?, cover_path),
                   annotations_path = COALESCE(?, annotations_path)
             WHERE {_HELD}
            """,
            (
                BookStatus.DOWNLOADED,
                encoding_format,
                _utcnow(),
                file_path,
                pdf_path,
                cover_path,
                annotations_path,
                book_id,
                claim,
            ),
        )
        conn.commit()
    return cursor.rowcount == 1


# --- book management ---------------------------------------------------------------
# What the API's per-book actions change. The file deletion itself is `library.py`'s;
# these record the outcome.


def clear_book_files(book_id: int) -> None:
    """Forget where a book was filed, after its files have been removed."""
    with closing(_get_connection()) as conn:
        conn.execute(
            """
            UPDATE library
               SET file_path = NULL, pdf_path = NULL, cover_path = NULL, annotations_path = NULL,
                   encoding_format = NULL, downloaded_at = NULL
             WHERE id = ?
            """,
            (book_id,),
        )
        conn.commit()


def set_monitored(book_id: int, monitored: bool) -> bool:
    """Whether the book is wanted. Returns False for an unknown book."""
    with closing(_get_connection()) as conn:
        cursor = conn.execute("UPDATE library SET monitored = ? WHERE id = ?", (monitored, book_id))
        conn.commit()
    return cursor.rowcount == 1


# Nothing on disk and no run holding it, so a row that can go without any tidying up
_UNDOWNLOADED = f"file_path IS NULL AND status NOT IN ('{BookStatus.DOWNLOADED}', '{BookStatus.DOWNLOADING}')"


def undownloaded_books_without_length(account_id: int) -> list[Book]:
    """
    One account's rows with no length and nothing on disk, and that no run holds: what a
    podcast stored as a book looks like. `sync._remove_stored_podcasts` asks Audible
    which they are.
    """
    with closing(_get_connection()) as conn:
        rows = conn.execute(
            f"SELECT * FROM library WHERE account_id = ? AND (length IS NULL OR length = 0) AND {_UNDOWNLOADED}",
            (account_id,),
        ).fetchall()
    return [Book.from_row(row) for row in rows]


def delete_undownloaded_book(book_id: int) -> bool:
    """
    Remove a library row that has nothing on disk, unless a run has since taken it or
    downloaded it. Returns whether it went. A run's record of the book keeps its own
    copy of the title, and reads `book_id` as None from here on.
    """
    with closing(_get_connection()) as conn:
        cursor = conn.execute(f"DELETE FROM library WHERE id = ? AND {_UNDOWNLOADED}", (book_id,))
        conn.commit()
        return cursor.rowcount > 0


def reset_for_download(book_id: int, *, monitored: bool) -> bool:
    """
    Put a book back at the start of the state machine: `waiting_download`, no attempts,
    no error, and wanted or not as asked.

    Refuses a book that is `downloading` - a run holds it, and yanking the row from
    under it would have the run's own bookkeeping overwrite this. Returns whether the
    row changed, so the caller can tell "busy" from "no such book" by looking first.
    """
    with closing(_get_connection()) as conn:
        cursor = conn.execute(
            """
            UPDATE library
               SET status = ?, attempts = 0, last_error = NULL, monitored = ?
             WHERE id = ? AND status != ?
            """,
            (BookStatus.WAITING_DOWNLOAD, monitored, book_id, BookStatus.DOWNLOADING),
        )
        conn.commit()
    return cursor.rowcount == 1


# The parked books `requeue_unavailable` queues again: `library_stats` counts the same
_RETRYABLE_UNAVAILABLE = "status = 'unavailable' AND is_consumable IS NOT 0"


def requeue_unavailable(account_id: int | None = None) -> int:
    """
    Queue every parked book Audible still reports consumable again, and return how many.

    A book is parked `unavailable` for one of two reasons, and only one of them heals
    on its own. A title Audible itself withdrew (`is_consumable` 0) is restored by the
    sync when the API offers it again. A title the API calls consumable but Audible
    refused a licence for stays parked until somebody asks: the refusal may be the
    title's (retired to streaming only) or the device's (the October 2026 registrations)
    and the book cannot tell which. This is the asking, for the whole shelf at once -
    the API's bulk retry, and what `replace_account_auth` does for a new device, when
    the refusals were the device's and a retry is exactly what is wanted.

    Withdrawn titles are left to the sync: queuing them is a guaranteed refusal, and a
    row of those would trip the circuit breaker. `is_consumable` NULL (a row written
    before the column) fails open here as everywhere. `monitored` is not touched: an
    unmonitored book is still not wanted, and `reset_for_download` is for changing that
    one book at a time. One account's books, or everyone's.
    """
    where = _RETRYABLE_UNAVAILABLE
    params: list = []
    if account_id is not None:
        where += " AND account_id = ?"
        params.append(account_id)
    with closing(_get_connection()) as conn:
        cursor = conn.execute(
            f"UPDATE library SET status = ?, attempts = 0, last_error = NULL WHERE {where}",
            (BookStatus.WAITING_DOWNLOAD, *params),
        )
        conn.commit()
    return cursor.rowcount


# The columns a listing may be sorted by, keyed by the name the API accepts. `author`
# sorts on the JSON text of the list, which puts the first author first - good enough
# for a shelf.
BOOK_SORT_COLUMNS = {
    "date_added": "date_added",
    "title": "title",
    "author": "authors",
    "release_date": "release_date",
    "downloaded_at": "downloaded_at",
}


def _book_filter(
    *,
    account_id: int | None = None,
    status: BookStatus | str | None = None,
    monitored: bool | None = None,
    q: str | None = None,
) -> tuple[str, list]:
    """
    The `WHERE` clause and its parameters for the library filters.

    Shared by `list_books` and `book_ids_matching`, so a bulk action on "everything
    matching these filters" acts on exactly the books the listing showed.
    """
    where = []
    params: list = []
    if account_id is not None:
        where.append("account_id = ?")
        params.append(account_id)
    if status is not None:
        where.append("status = ?")
        params.append(status)
    if monitored is not None:
        where.append("monitored = ?")
        params.append(monitored)
    if q:
        like = f"%{q}%"
        where.append("(title LIKE ? OR authors LIKE ? OR series LIKE ?)")
        params.extend([like, like, like])
    return (f" WHERE {' AND '.join(where)}" if where else ""), params


def list_books(
    *,
    account_id: int | None = None,
    status: BookStatus | str | None = None,
    monitored: bool | None = None,
    q: str | None = None,
    sort: str = "date_added",
    descending: bool = True,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Book], int]:
    """
    A page of the library and the total that matched, for the API.

    `q` matches the title, any author or any series, case-insensitively for ASCII (a
    `LIKE` over the JSON text is enough at the size of a personal library).

    Raises:
        ValueError: for a `sort` not in `BOOK_SORT_COLUMNS`
    """
    if sort not in BOOK_SORT_COLUMNS:
        raise ValueError(f"sort must be one of {', '.join(BOOK_SORT_COLUMNS)}, got {sort!r}")

    clause, params = _book_filter(account_id=account_id, status=status, monitored=monitored, q=q)
    order = f" ORDER BY {BOOK_SORT_COLUMNS[sort]} {'DESC' if descending else 'ASC'}, id"

    with closing(_get_connection()) as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM library{clause}", params).fetchone()[0]
        rows = conn.execute(f"SELECT * FROM library{clause}{order} LIMIT ? OFFSET ?", (*params, limit, offset))
        return [Book.from_row(row) for row in rows], total


def book_ids_matching(
    *,
    account_id: int | None = None,
    status: BookStatus | str | None = None,
    monitored: bool | None = None,
    q: str | None = None,
) -> list[int]:
    """The row ids of every book the same filters as `list_books` match, in id order."""
    clause, params = _book_filter(account_id=account_id, status=status, monitored=monitored, q=q)
    with closing(_get_connection()) as conn:
        return [row[0] for row in conn.execute(f"SELECT id FROM library{clause} ORDER BY id", params)]


def library_stats(*, account_id: int | None = None) -> dict:
    """
    Counts for a dashboard: books by status, wanted or not, and the total.

    Every `BookStatus` appears in `by_status` and `monitored_by_status` (the monitored
    books alone, what the listing's `monitored=true` filter counts), zero when empty, so
    a UI has a stable shape to draw from. `retryable_unavailable` is how many parked
    books `requeue_unavailable` would queue again - the licence refusals, not the titles
    Audible withdrew. A row whose status this build does not know counts towards the
    totals only.
    """
    clause = "" if account_id is None else " WHERE account_id = ?"
    params: tuple = () if account_id is None else (account_id,)
    by_status = {status.value: 0 for status in BookStatus}
    monitored_by_status = dict(by_status)
    total = wanted = 0
    with closing(_get_connection()) as conn:
        rows = conn.execute(
            f"SELECT status, COUNT(*), COALESCE(SUM(monitored = 1), 0) FROM library{clause} GROUP BY status", params
        )
        for status, count, monitored in rows:
            total += count
            wanted += monitored
            if status in by_status:
                by_status[status] = count
                monitored_by_status[status] = monitored
        retryable = conn.execute(
            f"SELECT COUNT(*) FROM library WHERE {_RETRYABLE_UNAVAILABLE}"
            + ("" if account_id is None else " AND account_id = ?"),
            params,
        ).fetchone()[0]
    return {
        "by_status": by_status,
        "monitored_by_status": monitored_by_status,
        "monitored": wanted,
        "unmonitored": total - wanted,
        "total": total,
        "retryable_unavailable": retryable,
    }


def downloaded_file_paths(*, account_id: int | None = None) -> list[str]:
    """Every recorded `file_path`, for adding up what the library takes on disk."""
    clause = " AND account_id = ?" if account_id is not None else ""
    params: tuple = (account_id,) if account_id is not None else ()
    with closing(_get_connection()) as conn:
        rows = conn.execute(f"SELECT file_path FROM library WHERE file_path IS NOT NULL{clause}", params)
        return [row[0] for row in rows]


# --- sync_runs -------------------------------------------------------------------
# Grouped by table rather than split across the readers and writers above: the four
# functions below are only meaningful together, and only `latest_successful_sync_start`
# is read by the pipeline itself.

# A run whose sync finished is a valid cursor even if its downloads did not, so all four
# count: a cancel or an abort only happens after the sync (see `SyncOutcome`). A run still
# `running`, or one that failed before reading the library, does not.
_CURSOR_OUTCOMES = (SyncOutcome.SUCCESS, SyncOutcome.PARTIAL, SyncOutcome.CANCELLED, SyncOutcome.ABORTED)


def start_sync_run(account_id: int | None = None, *, full_refresh: bool = False) -> int:
    """
    Open a run row for one account and return its id.

    Written before anything is fetched, so a run that is killed leaves a row that is
    still `running` with no `finished_at` - the record that tells a run which died half
    way from one that completed and found nothing. `full_refresh` is whether the run
    sets out to read the whole library; `finish_sync_run` records whether it did.
    """
    with closing(_get_connection()) as conn:
        cursor = conn.execute(
            "INSERT INTO sync_runs (account_id, started_at, outcome, books_recorded, full_refresh) "
            "VALUES (?, ?, ?, 1, ?)",
            (account_id, _utcnow(), SyncOutcome.RUNNING, full_refresh),
        )
        conn.commit()
        return cursor.lastrowid


def finish_sync_run(
    run_id: int,
    *,
    outcome: SyncOutcome,
    books_seen: int = 0,
    books_added: int = 0,
    books_downloaded: int = 0,
    books_failed: int = 0,
    error: str | None = None,
    full_refresh: bool | None = None,
) -> None:
    """
    Close a run row with its outcome and counters.

    Args:
        run_id: The id `start_sync_run` returned
        outcome: How the run ended. Only `success` and `partial` become a cursor for
            the next run, because only those read the library through
        books_seen: Books the incremental fetch returned
        books_added: Books that fetch inserted
        books_downloaded: Books downloaded, decrypted and filed
        books_failed: Books that were tried and did not finish
        error: The exception that stopped the run, if one did
        full_refresh: Whether the run read the whole library; None leaves what
            `start_sync_run` recorded, which is what a run that failed before reading says
    """
    with closing(_get_connection()) as conn:
        conn.execute(
            """
            UPDATE sync_runs
            SET finished_at = ?,
                outcome = ?,
                books_seen = ?,
                books_added = ?,
                books_downloaded = ?,
                books_failed = ?,
                error = ?,
                full_refresh = COALESCE(?, full_refresh)
            WHERE id = ?
            """,
            (_utcnow(), outcome, books_seen, books_added, books_downloaded, books_failed, error, full_refresh, run_id),
        )
        conn.commit()


def latest_successful_sync_start(account_id: int | None = None) -> str | None:
    """
    When one account's last run that read the library through started, or None.

    This is the incremental sync cursor. It is the run's **start** rather than its
    finish because anything Audible added while the run was reading has to be picked up
    next time; `sync.py` widens it further to absorb clock skew.

    `partial` counts alongside `success`: its sync completed, and a book that failed to
    download is tracked by the library state machine, not by the cursor.

    Only the account's own rows count. Runs written before there were accounts are
    given to the account the migration attaches the library to
    (`_migrate_library_to_accounts`); counting unowned rows for every account made an
    account added later start from that library's cursor and never fetch its own back
    catalogue. `IS` rather than `=` so `account_id=None` still reads unowned rows.
    """
    placeholders = ", ".join("?" for _ in _CURSOR_OUTCOMES)
    with closing(_get_connection()) as conn:
        return conn.execute(
            f"""
            SELECT MAX(started_at) FROM sync_runs
             WHERE outcome IN ({placeholders})
               AND account_id IS ?
            """,
            (*_CURSOR_OUTCOMES, account_id),
        ).fetchone()[0]


def latest_full_refresh_start(account_id: int) -> str | None:
    """
    When one account's last full refresh that read the library through started, or None.

    What the daily full refresh is due from. Kept per account, so an account added later
    or one whose refresh failed is not skipped because another's ran; a run that failed
    before reading does not count, and one asked for by hand does, like any other.
    """
    placeholders = ", ".join("?" for _ in _CURSOR_OUTCOMES)
    with closing(_get_connection()) as conn:
        return conn.execute(
            f"""
            SELECT MAX(started_at) FROM sync_runs
             WHERE outcome IN ({placeholders})
               AND account_id = ?
               AND full_refresh = 1
            """,
            (*_CURSOR_OUTCOMES, account_id),
        ).fetchone()[0]


def latest_sync_run_start() -> str | None:
    """
    When the newest run started, whatever became of it, or None if there has never been one.

    This is what the scheduler paces itself from: a run that failed or was cancelled
    still counts as "we tried", so the next attempt waits the full interval rather than
    hammering Audible every tick while something is broken.
    """
    with closing(_get_connection()) as conn:
        return conn.execute("SELECT MAX(started_at) FROM sync_runs").fetchone()[0]


def get_sync_runs(limit: int = 20, offset: int = 0, *, account_id: int | None = None) -> list[SyncRun]:
    """
    Run history, newest first, for one account or all.

    Unused by the pipeline: this is the read the API and the UI are for, and `offset`
    is how a history view pages.
    """
    sql = "SELECT * FROM sync_runs"
    params: tuple = ()
    if account_id is not None:
        sql = f"{sql} WHERE account_id = ?"
        params = (account_id,)
    with closing(_get_connection()) as conn:
        rows = conn.execute(f"{sql} ORDER BY started_at DESC, id DESC LIMIT ? OFFSET ?", (*params, limit, offset))
        return [SyncRun.from_row(row) for row in rows]


def get_sync_run(run_id: int) -> SyncRun | None:
    with closing(_get_connection()) as conn:
        row = conn.execute("SELECT * FROM sync_runs WHERE id = ?", (run_id,)).fetchone()
    return SyncRun.from_row(row) if row is not None else None


def record_run_book(
    run_id: int,
    book: Book,
    outcome: RunBookOutcome | str,
    *,
    error: str | None = None,
    bytes: int | None = None,
    seconds: float | None = None,
) -> None:
    """Record what became of one book in a run, for the run's detail on the Activity screen."""
    with closing(_get_connection()) as conn:
        conn.execute(
            "INSERT INTO sync_run_books (run_id, book_id, asin, title, outcome, error, bytes, seconds, finished_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, book.id, book.asin, book.title, RunBookOutcome(outcome), error, bytes, seconds, _utcnow()),
        )
        conn.commit()


def get_run_books(run_id: int) -> list[RunBook]:
    """
    The books a run recorded, in the order it finished them. `book_id` is None once the
    book's row is gone; `cover_url` is the library's, when the row is still there.
    """
    with closing(_get_connection()) as conn:
        rows = conn.execute(
            """
            SELECT r.*, l.id AS live_id, l.cover_url
              FROM sync_run_books r
              LEFT JOIN library l ON l.id = r.book_id
             WHERE r.run_id = ?
             ORDER BY r.id
            """,
            (run_id,),
        )
        return [RunBook.from_row(row) for row in rows]


def count_sync_runs(*, account_id: int | None = None) -> int:
    with closing(_get_connection()) as conn:
        if account_id is None:
            return conn.execute("SELECT COUNT(*) FROM sync_runs").fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM sync_runs WHERE account_id = ?", (account_id,)).fetchone()[0]


# --- accounts ------------------------------------------------------------------
# One row per marketplace login. The credentials are stored as the JSON of
# `Authenticator.to_dict()`, encrypted with the process's secret key (`audible_sync.security.crypto`);
# `audible_sync.audible_api.accounts` is the only thing that builds an `Authenticator` from the decoded
# blob, this layer keeps the text and does the encrypting.

# The settings-table key the API token is stored under, encrypted like the credentials
API_TOKEN_SETTING = "api_token"
# ... and the web app password's hash (`audible_sync.security.webauth`)
WEB_PASSWORD_SETTING = "web_password"

# Accounts whose credentials do not decrypt, logged once per process rather than on
# every poll of the status endpoint
_undecryptable_logged: set[int] = set()


def _encode_auth(auth: dict | None) -> str | None:
    return crypto.encrypt(json.dumps(auth)) if auth is not None else None


def _account_from_row(row: sqlite3.Row) -> Account:
    """
    An `Account` with its credentials decrypted.

    A blob that does not decrypt - the key changed - reads as no credentials, so the
    account shows `needs_login` and the pipeline skips it, **without** touching the
    stored blob: restoring the right key brings the login back, and a re-login
    replaces it either way.
    """
    data = dict(row)
    stored = data.get("auth")
    if stored:
        try:
            data["auth"] = crypto.decrypt(stored)
        except crypto.WrongKey as error:
            if data["id"] not in _undecryptable_logged:
                _undecryptable_logged.add(data["id"])
                logger.error("The credentials of account %s cannot be read: %s", data["id"], error)
            data["auth"] = None
    return Account.from_row(data)


def encrypt_stored_secrets() -> int:
    """
    Encrypt every secret still stored in plain text, and return how many were.

    The one-off migration for a database written before there was a key: the
    credentials of each account and the API token. Run at every start after the key is
    configured; a database that is already encrypted is left alone. Idempotent.
    """
    rewritten = 0
    with closing(_get_connection()) as conn:
        for row in conn.execute("SELECT id, auth FROM accounts WHERE auth IS NOT NULL").fetchall():
            if not crypto.is_encrypted(row["auth"]):
                conn.execute("UPDATE accounts SET auth = ? WHERE id = ?", (crypto.encrypt(row["auth"]), row["id"]))
                rewritten += 1
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (API_TOKEN_SETTING,)).fetchone()
        if row is not None and row["value"] and not crypto.is_encrypted(row["value"]):
            conn.execute(
                "UPDATE settings SET value = ?, updated_at = ? WHERE key = ?",
                (crypto.encrypt(row["value"]), _utcnow(), API_TOKEN_SETTING),
            )
            rewritten += 1
        conn.commit()
    if rewritten:
        logger.info("Encrypted %d stored secret(s) that were in plain text", rewritten)
    return rewritten


def customer_id_of(customer_info: dict | None) -> str | None:
    """
    The customer a login belongs to: `user_id` from Amazon's `customer_info`, or None
    when it does not say. An empty id is no id, or it would be stored and every login
    that does not say would collide in the unique index.
    """
    return (customer_info or {}).get("user_id") or None


def backfill_customer_ids() -> int:
    """
    Give each account stored before there was a `customer_id` column its customer, read
    from its credentials, and return how many were.

    Run at every start after the key is configured. An account whose credentials do not
    say, or cannot be read, stays NULL; with the right key it is filled in on a later
    start, and a login fills it in either way.

    Two accounts holding the same login (the same customer in the same marketplace),
    stored before the rule was enforced, cannot both have it: the unique index refuses
    the second. The oldest gets it, and so keeps receiving that customer's logins, as
    it did before; the others are left as they are, books and all, and a warning says
    which to remove. Idempotent.
    """
    filled = 0
    with closing(_get_connection()) as conn:
        rows = conn.execute("SELECT * FROM accounts ORDER BY id").fetchall()
        held = {
            (row["customer_id"], (row["country_code"] or "").lower()): row["id"]
            for row in rows
            if row["customer_id"] is not None
        }
        for row in rows:
            if row["customer_id"] is not None or not row["auth"]:
                continue
            customer_id = customer_id_of((_account_from_row(row).auth or {}).get("customer_info"))
            if customer_id is None:
                continue
            key = (customer_id, (row["country_code"] or "").lower())
            if key in held:
                _warn_duplicate(held[key], row["id"])
                continue
            try:
                conn.execute("UPDATE accounts SET customer_id = ? WHERE id = ?", (customer_id, row["id"]))
            except sqlite3.IntegrityError:
                # Another process stored this customer on another account since the
                # SELECT; that one holds it now, and a start must not fail over it
                logger.info("Account %d's login was stored on another account meanwhile; left as it is", row["id"])
                continue
            held[key] = row["id"]
            filled += 1
        conn.commit()
    return filled


def _warn_duplicate(first: int, second: int) -> None:
    """Name two accounts holding one login, and the newer as the one to remove."""
    older, newer = sorted((first, second))
    logger.warning(
        "Accounts %d and %d hold the same Audible login, so both download the same library; "
        "remove account %d, the newer",
        older,
        newer,
        newer,
    )


def get_account_by_customer(customer_id: str, country_code: str) -> Account | None:
    """
    The account holding one customer's login in one marketplace, if any. Found by the
    plain `customer_id`, so it is found even when its credentials cannot be read.
    """
    with closing(_get_connection()) as conn:
        row = conn.execute(
            "SELECT * FROM accounts WHERE customer_id = ? AND lower(country_code) = lower(?)",
            (customer_id, country_code),
        ).fetchone()
    return _account_from_row(row) if row is not None else None


def count_unreadable_secrets() -> int:
    """
    How many stored secrets the configured key cannot decrypt.

    Non-zero means this process holds another key than the one that wrote them - a CLI
    on the host that generated its own `secrets/secret.key` while the service's key is
    in `AUDIBLE_SYNC_SECRET_KEY`, say. Anything it stores now is unreadable to the other.
    """
    with closing(_get_connection()) as conn:
        values = [row["auth"] for row in conn.execute("SELECT auth FROM accounts WHERE auth IS NOT NULL")]
        values += [
            row["value"]
            for row in conn.execute(
                "SELECT value FROM settings WHERE key IN (?, ?)", (API_TOKEN_SETTING, WEB_PASSWORD_SETTING)
            )
        ]
    unreadable = 0
    for value in values:
        try:
            crypto.decrypt(value)
        except crypto.WrongKey:
            unreadable += 1
    return unreadable


def add_account(
    name: str,
    country_code: str,
    *,
    auth: dict | None,
    customer_name: str | None = None,
    customer_id: str | None = None,
    monitor_existing: bool = True,
) -> int:
    """
    Create an account and return its id. `auth` None means it still needs a login.

    Raises `sqlite3.IntegrityError` when another account already holds `customer_id`'s
    login in this marketplace.
    """
    with closing(_get_connection()) as conn:
        cursor = conn.execute(
            """
            INSERT INTO accounts (name, country_code, customer_name, customer_id, auth, monitor_existing, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                country_code,
                customer_name,
                customer_id,
                _encode_auth(auth),
                monitor_existing,
                _utcnow(),
            ),
        )
        conn.commit()
        return cursor.lastrowid


def get_accounts() -> list[Account]:
    """Every account, oldest first."""
    with closing(_get_connection()) as conn:
        return [_account_from_row(row) for row in conn.execute("SELECT * FROM accounts ORDER BY id")]


def get_account(account_id: int) -> Account | None:
    with closing(_get_connection()) as conn:
        row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    return _account_from_row(row) if row is not None else None


def update_account(
    account_id: int,
    *,
    name: str | None = None,
    country_code: str | None = None,
    customer_name: str | None = None,
    enabled: bool | None = None,
) -> None:
    """Change the given fields and leave the rest alone."""
    updates = []
    values: list = []
    for column, value in (
        ("name", name),
        ("country_code", country_code),
        ("customer_name", customer_name),
        ("enabled", enabled),
    ):
        if value is not None:
            updates.append(f"{column} = ?")
            values.append(value)
    if not updates:
        return
    values.append(account_id)
    with closing(_get_connection()) as conn:
        conn.execute(f"UPDATE accounts SET {', '.join(updates)} WHERE id = ?", values)
        conn.commit()


def save_account_auth(
    account_id: int,
    auth: dict | None,
    *,
    country_code: str | None = None,
    customer_name: str | None = None,
    customer_id: str | None = None,
) -> bool:
    """
    Store the credentials, or clear them (None) so the account reads as needing a login.
    `country_code` and `customer_name`, when given, go in the same statement: what a login
    says about the account must not be kept when its credentials are not. Likewise
    `customer_id`; nothing clears it, since the account's library stays that customer's.

    Returns whether the account was there to store them on. Raises
    `sqlite3.IntegrityError` when another account already holds `customer_id`'s login
    in the marketplace.
    """
    with closing(_get_connection()) as conn:
        cursor = conn.execute(
            "UPDATE accounts SET auth = ?, country_code = COALESCE(?, country_code), "
            "customer_name = COALESCE(?, customer_name), customer_id = COALESCE(?, customer_id) WHERE id = ?",
            (_encode_auth(auth), country_code, customer_name, customer_id, account_id),
        )
        conn.commit()
        return cursor.rowcount == 1


def replace_account_auth_if_unchanged(account_id: int, expected: dict | None, auth: dict) -> bool:
    """
    Store `auth` only if the stored credentials are still `expected`; return whether it did.

    What a run writing back the token it refreshed goes through. A re-login can replace
    an account's credentials while a run that started with the old ones is still going;
    a blind write at the end of that run would put the old device - deregistered by the
    re-login - back over the new one. The read and the write share one write-locked
    transaction, so nothing can slip in between them.
    """
    with closing(_get_connection()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT id, auth FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if row is None or _account_from_row(row).auth != expected:
            conn.rollback()
            return False
        conn.execute("UPDATE accounts SET auth = ? WHERE id = ?", (_encode_auth(auth), account_id))
        conn.commit()
        return True


def mark_account_synced(account_id: int) -> None:
    with closing(_get_connection()) as conn:
        conn.execute("UPDATE accounts SET last_synced_at = ? WHERE id = ?", (_utcnow(), account_id))
        conn.commit()


def delete_account(account_id: int) -> None:
    """
    Remove an account with its library rows and run history.

    The files on disk are left alone: they are the user's, and a re-added account files
    the same book to the same path and reuses them.

    Removing the account that held a customer frees it for a duplicate from before the
    rule, which `backfill_customer_ids` left without one: it gets it now rather than at
    the next start, so a login meanwhile finds it instead of adding a third account.
    """
    with closing(_get_connection()) as conn:
        conn.execute("DELETE FROM library WHERE account_id = ?", (account_id,))
        conn.execute(
            "DELETE FROM sync_run_books WHERE run_id IN (SELECT id FROM sync_runs WHERE account_id = ?)",
            (account_id,),
        )
        conn.execute("DELETE FROM sync_runs WHERE account_id = ?", (account_id,))
        conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        conn.commit()
    backfill_customer_ids()


# --- settings ------------------------------------------------------------------
# Text in, text out: `audible_sync.settings` is the only reader and writer, and it owns the
# parsing, the defaults and the validation. Keeping this layer typeless means a value
# the running build cannot parse is still stored and read back rather than lost.


def get_settings() -> dict[str, str]:
    """Every stored setting, keyed by name. Empty on a database that has never been seeded."""
    with closing(_get_connection()) as conn:
        return {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM settings")}


def save_settings(values: dict[str, str]) -> None:
    """
    Write settings, replacing any that already exist.

    An upsert per key rather than a wipe and rewrite, so a partial update from the API
    leaves every other setting exactly as it was. `updated_at` is stamped on the keys
    written, which is what tells a UI when each value last changed.
    """
    if not values:
        return

    now = _utcnow()
    with closing(_get_connection()) as conn:
        conn.executemany(
            """
            INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            [(key, value, now) for key, value in values.items()],
        )
        conn.commit()


def has_settings() -> bool:
    """Whether anything has ever been stored, which is what decides if config.ini is seeded."""
    with closing(_get_connection()) as conn:
        return bool(conn.execute("SELECT EXISTS(SELECT 1 FROM settings)").fetchone()[0])


# --- sessions ------------------------------------------------------------------
# Web app sign-ins, keyed by the hash of the cookie's token. `audible_sync.security.webauth` owns the
# lifetimes and the hashing; this layer stores and compares the ISO 8601 text.


def add_session(session_id: str, *, persistent: bool, created_at: str, expires_at: str) -> None:
    with closing(_get_connection()) as conn:
        conn.execute(
            "INSERT INTO sessions (id, persistent, created_at, last_seen_at, expires_at) VALUES (?, ?, ?, ?, ?)",
            (session_id, persistent, created_at, created_at, expires_at),
        )
        conn.commit()


def get_session(session_id: str) -> dict | None:
    """The session's row as a dict (`persistent` a bool), or None."""
    with closing(_get_connection()) as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        return None
    session = dict(row)
    session["persistent"] = bool(session["persistent"])
    return session


def touch_session(session_id: str, *, last_seen_at: str, expires_at: str) -> None:
    with closing(_get_connection()) as conn:
        conn.execute(
            "UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE id = ?",
            (last_seen_at, expires_at, session_id),
        )
        conn.commit()


def delete_session(session_id: str) -> None:
    with closing(_get_connection()) as conn:
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()


def delete_sessions(*, keep: str | None = None) -> int:
    """End every session, or every one but `keep`; return how many went."""
    with closing(_get_connection()) as conn:
        cursor = conn.execute("DELETE FROM sessions WHERE id IS NOT ?", (keep,))
        conn.commit()
        return cursor.rowcount


def delete_expired_sessions(now: str) -> int:
    with closing(_get_connection()) as conn:
        cursor = conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        conn.commit()
        return cursor.rowcount
