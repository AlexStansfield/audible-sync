/**
 * Book detail (Book.dc.html): one book's details, its monitoring, the actions its status
 * allows, its downloads and its files. A book a run is downloading gets the live panel
 * (StateBookDownloading); one marked downloading that no run holds gets a note instead
 * (StateBookStale); an unmonitored book is dimmed with a note (StateBookUnmonitored).
 * A book the API does not have is the 404 page.
 */

import { useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router";
import { ApiError } from "../../api/client";
import { bookKey, useStatus } from "../../api/queries";
import type { components } from "../../api/schema";
import { useSyncActions } from "../../api/useSyncActions";
import { StatusChip } from "../../components/Chip";
import { Cover } from "../../components/Cover";
import { ConfirmDialog } from "../../components/Dialog";
import { ErrorCard } from "../../components/ErrorCard";
import { Icon } from "../../components/Icon";
import { ProgressBar } from "../../components/ProgressBar";
import { Skeleton } from "../../components/Skeleton";
import { Switch } from "../../components/Switch";
import { useToast } from "../../components/Toasts";
import { formatDate, formatLength, formatTime, formatTimeInSentence } from "../../lib/format";
import { positiveId } from "../../lib/ids";
import { useBook } from "../dashboard/queries";
import { bookOfRun } from "../dashboard/text";
import { liveBook } from "../library/live";
import { NotFoundPage } from "../NotFoundPage";
import styles from "./Book.module.css";
import { type BookActionName, useBookAction, useMaxAttempts, useMonitor } from "./queries";
import { attemptsText, fileRows, formatLabel, listeningText, problem, seriesLabel, transferText } from "./text";

type Book = components["schemas"]["BookOut"];
type RunSnapshot = components["schemas"]["RunSnapshot"];
type Account = components["schemas"]["AccountOut"];

export function BookPage() {
  const id = positiveId(useParams().id);
  if (id === null) return <NotFoundPage kind="book" />;
  // A new id is a new page: no notice or dialog carries over from the last book
  return <BookView key={id} bookId={id} />;
}

function BookView({ bookId }: { bookId: number }) {
  const book = useBook(bookId);
  const status = useStatus();
  const client = useQueryClient();

  const run = status.data?.current_run;
  const liveHere = run?.book?.id === bookId;

  // The run moving on from this book (or ending) changes it: read it again. Until that
  // read lands the cached row still says downloading with no run on it, which is not the
  // stale claim the note is about. A layout effect, so that moment is never painted.
  const wasLive = useRef(liveHere);
  const [rereading, setRereading] = useState(false);
  useLayoutEffect(() => {
    if (wasLive.current && !liveHere) {
      setRereading(true);
      void client.invalidateQueries({ queryKey: bookKey(bookId) });
    }
    wasLive.current = liveHere;
  }, [liveHere, bookId, client]);
  // Any answer to that read, the book or an error, ends the wait
  const answered = book.dataUpdatedAt + book.errorUpdatedAt;
  useEffect(() => {
    void answered;
    setRereading(false);
  }, [answered]);

  if (book.isError && !book.data) {
    if (book.error instanceof ApiError && book.error.status === 404) return <NotFoundPage kind="book" />;
    return <ErrorCard title="Could not load the book" error={book.error} onRetry={() => void book.refetch()} />;
  }
  if (!book.data) return <BookSkeleton />;
  return (
    <BookDetail
      book={book.data}
      run={liveHere ? (run ?? null) : null}
      // Only a status that has answered can say no run holds the book
      runKnown={status.data !== undefined && !rereading}
      account={status.data?.accounts.find((one) => one.id === book.data.account_id)}
    />
  );
}

function BookDetail({
  book,
  run,
  runKnown,
  account,
}: {
  book: Book;
  run: RunSnapshot | null;
  runKnown: boolean;
  account: Account | undefined;
}) {
  const maxAttempts = useMaxAttempts();
  const monitor = useMonitor(book.id);
  const action = useBookAction(book.id);
  const { cancel } = useSyncActions();
  const toast = useToast();
  // Redownload and Delete files both remove the files, so both confirm
  const [confirming, setConfirming] = useState<"redownload" | "delete-files" | null>(null);
  const [notice, setNotice] = useState<{ text: string; signIn?: boolean } | null>(null);

  // The row says downloading, or a run has it in hand before the row was read again
  const downloading = book.status === "downloading" || run !== null;
  const stale = downloading && run === null && runKnown;
  // A title Audible withdrew comes back on its own; a retry would be a certain refusal,
  // which counts towards the run's circuit breaker (the bulk retry skips it too)
  const canRetry = book.status === "failed" || (book.status === "unavailable" && book.is_consumable !== false);
  const shownStatus = run ? "downloading" : book.status;

  const act = (name: BookActionName) => {
    setNotice(null);
    action.mutate(name, {
      onSuccess: (result) => {
        setConfirming(null);
        if (name === "redownload") toast.success("The files are deleted and the book is queued for the next run.");
        if (name === "retry") toast.success("Queued for the next run.");
        if (name === "refresh") toast.success("Refreshed from Audible.");
        if (name === "delete-files") {
          const removed = "removed" in result ? result.removed.length : 0;
          toast.success(
            removed
              ? "Deleted the files. The book is unmonitored and stays in your library."
              : "There were no files to delete. The book is unmonitored and stays in your library.",
          );
        }
      },
      onError: (error) => {
        setConfirming(null);
        const status = error instanceof ApiError ? error.status : undefined;
        if (name === "refresh" && status === 409) {
          // The only 409 a refresh has: the account holds no working credentials
          setNotice({
            text: `${account?.name ?? "This book's account"} needs a sign-in before the book can be read from Audible.`,
            signIn: true,
          });
        } else if (status === 409) {
          // A run took the book since the page was read, or it is otherwise not allowed now
          setNotice({ text: error.message });
        } else if (name === "refresh" && status === 502) {
          toast.error("Audible did not answer. Try again in a minute.", error);
        } else {
          toast.error(ACTION_FAILED[name], error);
        }
      },
    });
  };

  const series = seriesLabel(book);
  const issue = problem(book);
  const busy = action.isPending;

  return (
    <>
      <nav aria-label="Breadcrumb" className={styles.breadcrumb}>
        <Link to="/library">Library</Link> / <span aria-current="page">{book.title}</span>
      </nav>

      <section className={styles.hero} aria-labelledby="book-title">
        <Cover bookId={book.id} className={`${styles.cover} ${book.monitored ? "" : styles.dimmed}`} />

        <div className={styles.body}>
          <div className={styles.chips}>
            <StatusChip status={shownStatus} />
            {account && (
              <>
                <span className={styles.cc}>{account.country_code.toUpperCase()}</span>
                <span className={styles.muted}>{account.name}</span>
              </>
            )}
            {!book.monitored && (
              <span className={styles.outlineChip}>
                <Icon name="bookmark" size={13} />
                Not monitored
              </span>
            )}
          </div>

          <div className={styles.titles}>
            <h1 id="book-title" className={styles.title}>
              {book.title}
            </h1>
            {book.subtitle && <p className={styles.subtitle}>{book.subtitle}</p>}
          </div>
          <Credits book={book} />

          <dl className={styles.details}>
            <Detail term="Series">{series ?? "—"}</Detail>
            <Detail term="Length">{formatLength(book.length) || "—"}</Detail>
            <Detail term="Released">{book.release_date ? formatDate(book.release_date) : "—"}</Detail>
            <Detail term="Added to Audible">{book.date_added ? formatDate(book.date_added) : "—"}</Detail>
            <Detail term="ASIN">
              <span className={styles.mono}>{book.asin}</span>
            </Detail>
            <Detail term="Genres">{book.genres.join(", ") || "—"}</Detail>
            <Detail term="Companion PDF">{book.has_pdf ? "Yes" : "None"}</Detail>
            <Detail term="Listening progress">
              <span className={styles.listening}>
                {!book.is_finished && (book.percent_complete ?? 0) > 0 && (
                  <span className={styles.listenTrack} aria-hidden="true">
                    <span style={{ width: `${Math.min(100, book.percent_complete)}%` }} />
                  </span>
                )}
                {listeningText(book)}
              </span>
            </Detail>
          </dl>

          {!book.monitored && (
            <div className={styles.note}>
              <Icon name="bookmark" size={16} />
              <span>
                This book is not monitored, so it will not be downloaded. Turn on Monitored to queue it for the next
                run.
              </span>
            </div>
          )}

          {run && (
            <LivePanel
              run={run}
              cancelling={run.stage === "cancelling" || cancel.isPending}
              onCancel={() => cancel.mutate()}
            />
          )}

          {stale && (
            <div role="status" className={styles.stale}>
              <Icon name="warning" size={18} className={styles.warnIcon} />
              <div className={styles.staleText}>
                <span className={styles.strong}>
                  {book.last_attempt_at
                    ? `Marked as downloading since ${formatTimeInSentence(book.last_attempt_at)}; no run is working on it.`
                    : "Marked as downloading; no run is working on it."}
                </span>
                <span className={styles.secondary}>
                  It is picked up again automatically, up to six hours after that time. This happens when a run was
                  stopped part way, or a command-line run is going beside the service. Its actions are unavailable until
                  then.
                </span>
              </div>
            </div>
          )}

          {notice && (
            <div role="alert" className={`alert-error ${styles.notice}`}>
              <span>{notice.text}</span>
              {notice.signIn && (
                <Link to="/accounts" className={styles.noticeLink}>
                  Sign in again
                </Link>
              )}
            </div>
          )}

          <div className={styles.actions}>
            <span className={styles.monitored}>
              <Switch
                checked={book.monitored}
                labelledBy="monitored-label"
                // A second change would save the first one's optimistic value to roll back to
                disabled={monitor.isPending}
                onChange={(monitored) => monitor.mutate(monitored)}
              />
              <span id="monitored-label">Monitored</span>
            </span>
            {(book.status === "downloaded" || downloading) && (
              <>
                <button
                  type="button"
                  className="btn btn-secondary"
                  disabled={downloading || busy}
                  onClick={() => setConfirming("redownload")}
                >
                  Redownload
                </button>
                <button
                  type="button"
                  className="btn btn-danger-outline"
                  disabled={downloading || busy}
                  onClick={() => setConfirming("delete-files")}
                >
                  Delete files
                </button>
              </>
            )}
            {!downloading && canRetry && (
              <button type="button" className="btn btn-primary" disabled={busy} onClick={() => act("retry")}>
                Retry download
              </button>
            )}
            <button
              type="button"
              className={`btn ${styles.outlineButton}`}
              disabled={downloading || busy}
              onClick={() => act("refresh")}
            >
              <Icon name="retry" size={16} />
              Refresh from Audible
            </button>
          </div>
        </div>
      </section>

      <div className={styles.cards}>
        <section aria-labelledby="dl-h" className={`${styles.card} ${styles.downloadCard}`}>
          <h2 id="dl-h" className="card-title">
            Download
          </h2>
          {issue && (
            <div className={styles.problem}>
              <span className={styles.problemTitle}>{issue.title}</span>
              {issue.note && <span className={styles.problemNote}>{issue.note}</span>}
              {issue.error && <code className={styles.problemText}>{issue.error}</code>}
            </div>
          )}
          <dl className={styles.downloadDetails}>
            <Detail term="Attempts">{attemptsText(book.attempts, maxAttempts.data)}</Detail>
            <Detail term="Last attempt">{book.last_attempt_at ? formatTime(book.last_attempt_at) : "—"}</Detail>
            <Detail term="Downloaded">{book.downloaded_at ? formatTime(book.downloaded_at) : "—"}</Detail>
            <Detail term="Format">{formatLabel(book.encoding_format)}</Detail>
          </dl>
        </section>

        <section aria-labelledby="files-h" className={`${styles.card} ${styles.filesCard}`}>
          <h2 id="files-h" className={`card-title ${styles.filesTitle}`}>
            Files on disk
          </h2>
          {fileRows(book).map((row) => (
            <div key={row.kind} className={styles.file}>
              <span className={styles.fileKind}>{row.kind}</span>
              <code className={row.path ? styles.path : styles.noPath} title={row.path ?? undefined}>
                {row.path ?? row.note}
              </code>
            </div>
          ))}
        </section>
      </div>

      <ConfirmDialog
        open={confirming === "delete-files"}
        title={`Delete the files for ${book.title}?`}
        confirmLabel="Delete files"
        cancelLabel="Keep files"
        busy={busy}
        onConfirm={() => act("delete-files")}
        onCancel={() => setConfirming(null)}
      >
        <p className={styles.dialogText}>
          The audio, cover and annotations are removed from your audiobooks folder, along with any folders left empty.
          The book is unmonitored so it will not download again. It stays in your library.
        </p>
      </ConfirmDialog>
      <ConfirmDialog
        open={confirming === "redownload"}
        title={`Redownload ${book.title}?`}
        confirmLabel="Delete and redownload"
        cancelLabel="Keep files"
        busy={busy}
        onConfirm={() => act("redownload")}
        onCancel={() => setConfirming(null)}
      >
        <p className={styles.dialogText}>
          The files are deleted now and the book is fetched again on the next run. If Audible refuses the licence then,
          the book is left without them until it can be downloaded.
        </p>
      </ConfirmDialog>
    </>
  );
}

const ACTION_FAILED: Record<BookActionName, string> = {
  redownload: "Could not redownload the book",
  retry: "Could not retry the book",
  "delete-files": "Could not delete the files",
  refresh: "Could not refresh the book",
};

/** By the authors (each a search of the library), narrated by the narrators. */
function Credits({ book }: { book: Book }) {
  return (
    <p className={styles.credits}>
      {book.authors.length > 0 && (
        <>
          By{" "}
          {book.authors.map((author, index) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: Audible can list one author twice
            <span key={index}>
              {index > 0 && ", "}
              <Link to={`/library?q=${encodeURIComponent(author)}`}>{author}</Link>
            </span>
          ))}
        </>
      )}
      {book.authors.length > 0 && book.narrators.length > 0 && " · "}
      {book.narrators.length > 0 && `Narrated by ${book.narrators.join(", ")}`}
    </p>
  );
}

function Detail({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div className={styles.detail}>
      <dt>{term}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function LivePanel({ run, cancelling, onCancel }: { run: RunSnapshot; cancelling: boolean; onCancel: () => void }) {
  const fraction = liveBook(run)?.fraction ?? null;
  const text = transferText(run.transfer);
  const place = bookOfRun(run);
  return (
    <div role="status" className={styles.live}>
      <div className={styles.liveHead}>
        <span className={styles.strong}>Downloading in run #{run.run_id}</span>
        {place && <span className={styles.muted}>{place}</span>}
        <button
          type="button"
          className={`btn btn-small ${styles.outlineButton} ${styles.cancel}`}
          disabled={cancelling}
          onClick={onCancel}
        >
          {cancelling ? "Cancelling…" : "Cancel run"}
        </button>
      </div>
      <ProgressBar label="Download progress" value={fraction === null ? null : fraction * 100} max={100} />
      {text && <span className={styles.transfer}>{text}</span>}
      <span className={styles.secondary}>
        A run is downloading this book, so its actions are unavailable until it finishes. Cancel the run to change it
        now; the book goes back to the queue.
      </span>
    </div>
  );
}

function BookSkeleton() {
  return (
    <section className={styles.hero} aria-label="Loading the book" aria-busy="true">
      <Skeleton style={{ width: 280, maxWidth: "100%", aspectRatio: "1", height: "auto", borderRadius: 10 }} />
      <div className={styles.body}>
        <Skeleton width={160} height={20} />
        <Skeleton width="60%" height={32} />
        <Skeleton width="40%" height={14} />
        <Skeleton width="100%" height={90} />
      </div>
    </section>
  );
}
