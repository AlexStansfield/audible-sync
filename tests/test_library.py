import shutil
from pathlib import Path

import pytest
from mutagen.mp4 import MP4

import src.database as database
from src import library
from src.library import BookBusy, audio_path_for, delete_book_files, delete_download, redownload, refresh, retry
from src.model import BookStatus
from tests.conftest import ACCOUNT_ID, make_book, make_settings

FIXTURE_M4B = Path(__file__).parent / "fixtures" / "silence.m4b"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "test.db"))
    database.init_db()
    database.add_account("Test (UK)", "uk", auth={"locale_code": "uk"})


def _m4b_with_asin(path: Path, asin: str) -> Path:
    """A real M4B carrying `comment=ASIN: ...`, the way every file the app writes does."""
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURE_M4B, path)
    audio = MP4(path)
    audio["\xa9cmt"] = [f"ASIN: {asin}"]
    audio.save()
    return path


def _settings(tmp_path):
    return make_settings(audiobook_folder=tmp_path / "audiobooks", download_folder=tmp_path / "dl")


# --- finding the audio -----------------------------------------------------------------


def test_audio_path_for_uses_the_recorded_path(tmp_path):
    book = make_book(file_path="/mnt/books/x.m4b")

    assert audio_path_for(book, _settings(tmp_path)) == Path("/mnt/books/x.m4b")


def test_audio_path_for_finds_a_legacy_book_where_the_templates_put_it(tmp_path):
    """A book downloaded before the path was recorded is where the downloader filed it."""
    settings = _settings(tmp_path)
    expected = _m4b_with_asin(settings.audiobook_folder / "Author One" / "Title" / "Title.m4b", "B001")

    assert audio_path_for(make_book("B001", file_path=None), settings) == expected


def test_audio_path_for_finds_the_asin_suffixed_variant(tmp_path):
    """Two books that rendered to the same name were filed side by side."""
    settings = _settings(tmp_path)
    _m4b_with_asin(settings.audiobook_folder / "Author One" / "Title" / "Title.m4b", "OTHER")
    expected = _m4b_with_asin(settings.audiobook_folder / "Author One" / "Title" / "Title [B001].m4b", "B001")

    assert audio_path_for(make_book("B001", file_path=None), settings) == expected


def test_audio_path_for_refuses_a_file_that_belongs_to_another_book(tmp_path):
    settings = _settings(tmp_path)
    _m4b_with_asin(settings.audiobook_folder / "Author One" / "Title" / "Title.m4b", "OTHER")

    assert audio_path_for(make_book("B001", file_path=None), settings) is None


def test_audio_path_for_honours_the_recorded_format(tmp_path):
    settings = _settings(tmp_path)
    folder = settings.audiobook_folder / "Author One" / "Title"
    folder.mkdir(parents=True)
    (folder / "Title.oga").write_bytes(b"not really ogg")

    # Unreadable tags are "no ASIN", so the oga is not claimed; and no m4b is looked for
    assert audio_path_for(make_book("B001", file_path=None, encoding_format="oga"), settings) is None


def test_audio_path_for_when_nothing_is_there(tmp_path):
    assert audio_path_for(make_book(file_path=None), _settings(tmp_path)) is None


# --- deleting -----------------------------------------------------------------------


def _filed_book(tmp_path, *, series=True):
    """A downloaded book with every file on disk, filed like the downloader would."""
    settings = _settings(tmp_path)
    folder = settings.audiobook_folder / "Author One" / ("Series/1 - Title" if series else "Title")
    folder.mkdir(parents=True)
    paths = {
        "file_path": folder / "Title.m4b",
        "pdf_path": folder / "Title.pdf",
        "cover_path": folder / "Title_cover.jpg",
        "annotations_path": folder / "Title_annotations.json",
    }
    for path in paths.values():
        path.write_bytes(b"x")
    book = make_book(status=BookStatus.DOWNLOADED, **{k: str(v) for k, v in paths.items()})
    return book, settings, folder


def test_delete_book_files_removes_everything_and_prunes_empty_folders(tmp_path):
    book, settings, folder = _filed_book(tmp_path)

    removed = delete_book_files(book, settings)

    assert sorted(removed) == sorted(
        Path(p) for p in (book.file_path, book.pdf_path, book.cover_path, book.annotations_path)
    )
    assert not folder.exists()
    assert not folder.parent.exists()  # the series folder, now empty
    assert not (settings.audiobook_folder / "Author One").exists()
    # Never the library root itself
    assert settings.audiobook_folder.is_dir()


def test_delete_book_files_leaves_a_folder_that_still_holds_something(tmp_path):
    book, settings, folder = _filed_book(tmp_path)
    (folder.parent / "2 - Sequel.m4b").write_bytes(b"y")

    delete_book_files(book, settings)

    assert not folder.exists()
    assert (folder.parent / "2 - Sequel.m4b").exists()
    assert folder.parent.exists()


def test_delete_book_files_tolerates_files_already_gone(tmp_path):
    book, settings, _ = _filed_book(tmp_path)
    Path(book.pdf_path).unlink()

    removed = delete_book_files(book, settings)

    assert Path(book.pdf_path) not in removed
    assert len(removed) == 3


def test_delete_book_files_with_nothing_recorded_removes_nothing(tmp_path):
    settings = _settings(tmp_path)
    settings.audiobook_folder.mkdir()

    assert delete_book_files(make_book(file_path=None), settings) == []


# --- the transitions ----------------------------------------------------------------


def _stored(tmp_path, **columns):
    """A book in the database, filed on disk, with the given columns forced."""
    book, settings, _ = _filed_book(tmp_path)
    database.update_books(ACCOUNT_ID, [book])
    stored = database.get_book_by_asin(book.asin)
    database.mark_book_downloaded(
        stored.id,
        "m4b",
        file_path=book.file_path,
        pdf_path=book.pdf_path,
        cover_path=book.cover_path,
        annotations_path=book.annotations_path,
    )
    if columns:
        assignments = ", ".join(f"{k} = ?" for k in columns)
        import sqlite3

        conn = sqlite3.connect(database.DB_FILE)
        conn.execute(f"UPDATE library SET {assignments} WHERE id = ?", (*columns.values(), stored.id))
        conn.commit()
        conn.close()
    return database.get_book(stored.id), settings


def test_delete_download_removes_the_files_and_unmonitors(db, tmp_path):
    book, settings = _stored(tmp_path, attempts=2)

    removed = delete_download(book, settings)

    assert len(removed) == 4
    after = database.get_book(book.id)
    assert after.status is BookStatus.WAITING_DOWNLOAD
    assert after.monitored is False
    assert (after.file_path, after.pdf_path, after.cover_path, after.annotations_path) == (None, None, None, None)
    assert (after.encoding_format, after.downloaded_at, after.attempts, after.last_error) == (None, None, 0, None)
    # Unmonitored, so not queued
    assert database.get_books_to_download() == []


def test_redownload_removes_the_files_and_queues_afresh(db, tmp_path):
    book, settings = _stored(tmp_path, attempts=3, last_error="old")

    removed = redownload(book, settings)

    assert len(removed) == 4
    after = database.get_book(book.id)
    assert (after.status, after.monitored, after.attempts, after.last_error) == (
        BookStatus.WAITING_DOWNLOAD,
        True,
        0,
        None,
    )
    assert [b.id for b in database.get_books_to_download()] == [book.id]


@pytest.mark.parametrize("action", [delete_download, redownload])
def test_file_actions_refuse_a_book_a_run_is_downloading(db, tmp_path, action):
    book, settings = _stored(tmp_path, status=BookStatus.DOWNLOADING)

    with pytest.raises(BookBusy, match="cancel the run first"):
        action(book, settings)

    assert Path(book.file_path).exists()


@pytest.mark.parametrize("action", ["delete", "redownload", "retry"])
def test_actions_refuse_a_book_a_run_claimed_after_it_was_read(db, tmp_path, action):
    """The `Book` in hand says waiting; the row is a run's by the time the reset lands."""
    book, settings = _stored(tmp_path, status=BookStatus.WAITING_DOWNLOAD)
    database.claim_book_for_download(book.id)

    with pytest.raises(BookBusy, match="claimed by a run"):
        if action == "delete":
            delete_download(book, settings)
        elif action == "redownload":
            redownload(book, settings)
        else:
            retry(book)

    assert database.get_book(book.id).status is BookStatus.DOWNLOADING


def test_retry_queues_a_failed_book_and_keeps_its_files(db, tmp_path):
    book, _ = _stored(tmp_path, status=BookStatus.FAILED, attempts=3, last_error="boom", monitored=0)

    retry(book)

    after = database.get_book(book.id)
    assert (after.status, after.monitored, after.attempts, after.last_error) == (
        BookStatus.WAITING_DOWNLOAD,
        True,
        0,
        None,
    )
    assert after.file_path == book.file_path
    assert Path(book.file_path).exists()


def test_retry_refuses_a_downloaded_book(db, tmp_path):
    book, _ = _stored(tmp_path)

    with pytest.raises(ValueError, match="use redownload"):
        retry(book)


def test_retry_refuses_a_book_a_run_is_downloading(db, tmp_path):
    book, _ = _stored(tmp_path, status=BookStatus.DOWNLOADING)

    with pytest.raises(BookBusy):
        retry(book)


# --- refresh ---------------------------------------------------------------------------


def test_refresh_re_reads_the_book_from_its_account(db, tmp_path, monkeypatch):
    book, _ = _stored(tmp_path)
    seen = {}

    class FakeAudible:
        def __init__(self, auth):
            seen["auth"] = auth
            self.auth = auth

        def get_book(self, asin):
            seen["asin"] = asin
            return make_book(asin, "Renamed", id=None, account_id=None)

    monkeypatch.setattr(library, "Audible", FakeAudible)
    monkeypatch.setattr(library, "authenticator_for", lambda account: f"auth-{account.id}")
    monkeypatch.setattr(
        library, "persist_auth_if_changed", lambda account, auth: seen.__setitem__("persisted", (account.id, auth))
    )

    fresh = refresh(book)

    assert seen == {
        "auth": f"auth-{ACCOUNT_ID}",
        "asin": book.asin,
        # A token the call refreshed is kept, as a run keeps it
        "persisted": (ACCOUNT_ID, f"auth-{ACCOUNT_ID}"),
    }
    assert fresh.id == book.id
    assert fresh.title == "Renamed"
    # The refresh is the sync's upsert: the download state is untouched
    assert fresh.status is BookStatus.DOWNLOADED
    assert fresh.file_path == book.file_path


def test_refresh_keeps_a_refreshed_token_even_when_audible_then_fails(db, tmp_path, monkeypatch):
    book, _ = _stored(tmp_path)
    persisted = []

    class FakeAudible:
        def __init__(self, auth):
            self.auth = auth

        def get_book(self, asin):
            raise RuntimeError("audible down")

    monkeypatch.setattr(library, "Audible", FakeAudible)
    monkeypatch.setattr(library, "authenticator_for", lambda account: "auth")
    monkeypatch.setattr(library, "persist_auth_if_changed", lambda account, auth: persisted.append(auth))

    with pytest.raises(RuntimeError, match="audible down"):
        refresh(book)

    assert persisted == ["auth"]


def test_refresh_refuses_a_book_whose_account_cannot_log_in(db, tmp_path):
    book, _ = _stored(tmp_path)
    database.save_account_auth(ACCOUNT_ID, None)

    with pytest.raises(ValueError, match="no credentials"):
        refresh(book)


# --- bulk ------------------------------------------------------------------------------


def _shelf(*asins, monitored=True):
    database.update_books(ACCOUNT_ID, [make_book(asin, f"Title {asin}") for asin in asins], monitor_new=monitored)
    return [database.get_book_by_asin(asin).id for asin in asins]


def _fail(book_id):
    database.claim_book_for_download(book_id)
    database.mark_book_failed(book_id, "boom", max_attempts=1)


def _park(book_id):
    database.claim_book_for_download(book_id)
    database.mark_book_unavailable(book_id, "Denied")


def test_bulk_monitor_counts_only_the_waiting_books_as_queued(db, tmp_path):
    waiting, failed, busy = _shelf("B001", "B002", "B003", monitored=False)
    _fail(failed)
    database.claim_book_for_download(busy)

    result = library.bulk("monitor", [waiting, failed, busy, 999], _settings(tmp_path))

    assert (result.updated, result.queued) == (2, 1)
    assert result.skipped == [(busy, library.SkipReason.BUSY), (999, library.SkipReason.NOT_FOUND)]
    assert database.get_book(waiting).monitored and database.get_book(failed).monitored
    # A failed book keeps its status until it is retried
    assert database.get_book(failed).status is BookStatus.FAILED
    assert not database.get_book(busy).monitored


def test_bulk_unmonitor(db, tmp_path):
    ids = _shelf("B001", "B002")

    result = library.bulk(library.BulkAction.UNMONITOR, ids, _settings(tmp_path))

    assert (result.updated, result.queued, result.skipped) == (2, 0, [])
    assert database.get_books_to_download() == []


def test_bulk_acts_on_a_duplicate_id_once(db, tmp_path):
    (book_id,) = _shelf("B001", monitored=False)

    result = library.bulk("monitor", [book_id, book_id], _settings(tmp_path))

    assert (result.updated, result.queued) == (1, 1)


def test_bulk_retry_queues_failed_and_parked_books_and_skips_downloaded_ones(db, tmp_path):
    downloaded, settings = _stored(tmp_path)
    failed, parked = _shelf("B002", "B003")
    _fail(failed)
    _park(parked)

    result = library.bulk("retry", [failed, parked, downloaded.id], settings)

    assert (result.updated, result.queued) == (2, 2)
    assert result.skipped == [(downloaded.id, library.SkipReason.DOWNLOADED)]
    assert {b.id for b in database.get_books_to_download()} == {failed, parked}


def test_bulk_delete_files_skips_a_book_with_none(db, tmp_path):
    filed, settings = _stored(tmp_path)
    (bare,) = _shelf("B002")

    result = library.bulk("delete_files", [filed.id, bare], settings)

    assert (result.updated, result.queued) == (1, 0)
    assert result.skipped == [(bare, library.SkipReason.NO_FILES)]
    assert not Path(filed.file_path).exists()
    assert database.get_book(filed.id).monitored is False
    # The skipped book is left exactly as it was
    assert database.get_book(bare).monitored is True


def test_bulk_skips_a_book_a_run_claims_after_it_was_read(db, tmp_path, monkeypatch):
    (book_id,) = _shelf("B001")
    _fail(book_id)
    stale = database.get_book(book_id)
    database.reset_for_download(book_id, monitored=True)
    database.claim_book_for_download(book_id)
    monkeypatch.setattr(library, "get_book", lambda _: stale)

    result = library.bulk("retry", [book_id], _settings(tmp_path))

    assert result.updated == 0
    assert result.skipped == [(book_id, library.SkipReason.BUSY)]
    assert database.get_book(book_id).status is BookStatus.DOWNLOADING


def test_bulk_refuses_an_unknown_action(db, tmp_path):
    with pytest.raises(ValueError):
        library.bulk("explode", [], _settings(tmp_path))


def test_book_files_lists_only_what_exists(tmp_path):
    book, settings, folder = _filed_book(tmp_path)
    Path(book.file_path).unlink()

    assert library.book_files(book, settings) == [
        folder / "Title.pdf",
        folder / "Title_cover.jpg",
        folder / "Title_annotations.json",
    ]


def test_bulk_skips_a_book_that_fails_and_carries_on(db, tmp_path, monkeypatch, caplog):
    first, broken, last = _shelf("B001", "B002", "B003", monitored=False)
    real = library.set_monitored

    def set_monitored(book_id, monitored):
        if book_id == broken:
            raise PermissionError("read-only")
        return real(book_id, monitored)

    monkeypatch.setattr(library, "set_monitored", set_monitored)

    result = library.bulk("monitor", [first, broken, last], _settings(tmp_path))

    assert result.updated == 2
    assert result.skipped == [(broken, library.SkipReason.ERROR)]
    assert database.get_book(last).monitored
    assert "read-only" in caplog.text


def test_bulk_retry_leaves_a_title_audible_withdrew_to_the_sync(db, tmp_path):
    """Each would be a certain refusal, and a queue of them trips the circuit breaker."""
    database.update_books(ACCOUNT_ID, [make_book("B001", is_consumable=False), make_book("B002")])
    withdrawn, refused = (database.get_book_by_asin(a).id for a in ("B001", "B002"))
    _park(withdrawn)
    _park(refused)

    result = library.bulk("retry", [withdrawn, refused], _settings(tmp_path))

    assert (result.updated, result.queued) == (1, 1)
    assert result.skipped == [(withdrawn, library.SkipReason.WITHDRAWN)]
    assert database.get_book(withdrawn).status is BookStatus.UNAVAILABLE


def test_bulk_delete_files_tidies_a_downloaded_book_whose_files_have_gone(db, tmp_path):
    book, settings = _stored(tmp_path)
    shutil.rmtree(settings.audiobook_folder)

    result = library.bulk("delete_files", [book.id], settings)

    assert (result.updated, result.skipped) == (1, [])
    after = database.get_book(book.id)
    assert (after.status, after.monitored, after.file_path) == (BookStatus.WAITING_DOWNLOAD, False, None)


def test_bulk_delete_files_looks_for_the_files_once(db, tmp_path, monkeypatch):
    book, settings = _stored(tmp_path)
    calls = []
    real = library.book_files
    monkeypatch.setattr(library, "book_files", lambda *a: calls.append(a) or real(*a))

    library.bulk("delete_files", [book.id], settings)

    assert len(calls) == 1
    assert not Path(book.file_path).exists()
