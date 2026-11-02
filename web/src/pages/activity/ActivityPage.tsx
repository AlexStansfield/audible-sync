/**
 * Activity (DESIGN.md, "Activity" and "Activity: books in a run"): the run history -
 * paged with Load more, filtered by account, each row opening onto the books it
 * downloaded, failed and was refused - and the service log, with a minimum level and
 * Follow. The account and the open run are in the URL (`?account=`, `?run=`), so the
 * Dashboard's recent runs can link straight to one.
 */
import { Fragment, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { type Status, useRefreshDuringRun, useStatus } from "../../api/queries";
import { Chip, OutcomeChip } from "../../components/Chip";
import { Cover } from "../../components/Cover";
import { ErrorCard } from "../../components/ErrorCard";
import { Icon } from "../../components/Icon";
import { Skeleton, SkeletonCard } from "../../components/Skeleton";
import { formatTime } from "../../lib/format";
import { useNow } from "../../lib/useNow";
import { isInterrupted } from "../dashboard/text";
import styles from "./Activity.module.css";
import { DURING_RUN, useLogs, useRefreshOnRunStart, useRunBooks, useRunHistory } from "./queries";
import {
  historySubtitle,
  LEVEL_COLOUR,
  LEVELS,
  type Level,
  logTime,
  type RunBook,
  runBookNote,
  runDuration,
  runNote,
  shownText,
} from "./text";

/** A positive whole number from the URL, else null. */
function idParam(value: string | null): number | null {
  if (!value || !/^\d+$/.test(value)) return null;
  const id = Number(value);
  return id > 0 && Number.isSafeInteger(id) ? id : null;
}

export function ActivityPage() {
  const [params, setParams] = useSearchParams();
  const status = useStatus();
  const accounts = status.data?.accounts ?? [];
  const accountId = idParam(params.get("account"));
  const openRun = idParam(params.get("run"));
  const history = useRunHistory(accountId);

  useRefreshOnRunStart(status.data);
  useRefreshDuringRun(status.data, DURING_RUN);

  const change = (key: "account" | "run", value: number | null) => {
    const next = new URLSearchParams(params);
    if (value === null) next.delete(key);
    else next.set(key, String(value));
    // Another account's history has other runs: none of them is open
    if (key === "account") next.delete("run");
    setParams(next, { replace: key === "run" });
  };

  const names = new Map(accounts.map((account) => [account.id, account.name]));
  const total = history.data?.pages[0]?.total;

  return (
    <div className={styles.page}>
      <header className="page-header">
        <div className={styles.heading}>
          <h1 className="page-title">Activity</h1>
          <span className="page-subtitle">
            {total === undefined ? (
              <Skeleton width={140} />
            ) : (
              historySubtitle(total, accountId ? (names.get(accountId) ?? null) : null)
            )}
          </span>
        </div>
        {accounts.length > 1 && (
          <label className={styles.account}>
            Account
            <select
              className="input"
              value={accountId ?? ""}
              onChange={(event) => change("account", idParam(event.target.value))}
            >
              <option value="">All accounts</option>
              {accounts.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.name}
                </option>
              ))}
            </select>
          </label>
        )}
      </header>

      <RunHistory
        history={history}
        status={status.data}
        names={names}
        openRun={openRun}
        onToggle={(id) => change("run", openRun === id ? null : id)}
      />
      <ServiceLog />
    </div>
  );
}

function RunHistory({
  history,
  status,
  names,
  openRun,
  onToggle,
}: {
  history: ReturnType<typeof useRunHistory>;
  status: Status | undefined;
  names: Map<number, string>;
  openRun: number | null;
  onToggle: (id: number) => void;
}) {
  const current = status?.current_run;
  const now = useNow(current != null);
  // The run the URL opens, brought into view once (a link from the Dashboard)
  const scrolled = useRef(false);
  useEffect(() => {
    if (scrolled.current || openRun === null || !history.data) return;
    scrolled.current = true;
    document.getElementById(`run-${openRun}`)?.scrollIntoView?.({ block: "center" });
  }, [openRun, history.data]);

  if (history.isPending) return <SkeletonCard rows={6} label="Loading the run history" />;
  if (history.isError) {
    return <ErrorCard title="Could not load the run history" error={history.error} onRetry={() => history.refetch()} />;
  }

  // A run that started between pages pushes one row onto the next page as well
  const seen = new Set<number>();
  const runs = history.data.pages.flatMap((page) => page.items).filter((run) => !seen.has(run.id) && seen.add(run.id));
  const total = history.data.pages[0]?.total ?? runs.length;

  return (
    <section aria-labelledby="runs-h" className={styles.card}>
      <h2 id="runs-h" className={styles.cardTitle}>
        Run history
      </h2>
      {runs.length === 0 ? (
        <p className={styles.empty}>No runs yet. The first one starts on the schedule, or with Sync now.</p>
      ) : (
        <div className={styles.scroll}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th scope="col">Run</th>
                <th scope="col">Outcome</th>
                <th scope="col">Account</th>
                <th scope="col">Started</th>
                <th scope="col">Duration</th>
                <th scope="col" className={styles.num}>
                  Seen
                </th>
                <th scope="col" className={styles.num}>
                  Added
                </th>
                <th scope="col" className={styles.num}>
                  Downloaded
                </th>
                <th scope="col" className={styles.num}>
                  Failed
                </th>
                <th scope="col">Note</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => {
                const open = openRun === run.id;
                const live = current?.run_id === run.id;
                return (
                  <Fragment key={run.id}>
                    <tr id={`run-${run.id}`} className={open ? styles.openRow : undefined}>
                      <td className={styles.runCell}>
                        <button
                          type="button"
                          className={styles.toggle}
                          aria-expanded={open}
                          aria-controls={open ? `run-${run.id}-books` : undefined}
                          aria-label={`Books in run ${run.id}`}
                          onClick={() => onToggle(run.id)}
                        >
                          <Icon
                            name="chevron"
                            size={14}
                            strokeWidth={2.4}
                            className={open ? styles.turned : undefined}
                          />
                          #{run.id}
                        </button>
                      </td>
                      <td>
                        {isInterrupted(run, current?.run_id) ? (
                          <Chip colour="var(--bad)">Interrupted</Chip>
                        ) : (
                          <OutcomeChip outcome={run.outcome} />
                        )}
                      </td>
                      <td>{run.account_id != null ? (names.get(run.account_id) ?? "—") : "—"}</td>
                      <td className={styles.secondary}>{formatTime(run.started_at)}</td>
                      <td className={styles.duration}>{runDuration(run, live, now)}</td>
                      <td className={styles.num}>{run.books_seen}</td>
                      <td className={styles.num}>{run.books_added}</td>
                      <td className={styles.num}>{run.books_downloaded}</td>
                      <td className={`${styles.num} ${run.books_failed ? styles.bad : ""}`}>{run.books_failed}</td>
                      <td className={styles.note}>{runNote(run, current)}</td>
                    </tr>
                    {open && (
                      <tr id={`run-${run.id}-books`} className={styles.booksRow}>
                        <td colSpan={10}>
                          <RunBooks runId={run.id} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {runs.length > 0 && (
        <div className={styles.foot}>
          <span>{shownText(runs.length, total)}</span>
          {history.hasNextPage && (
            <button
              type="button"
              className="btn btn-secondary btn-small"
              disabled={history.isFetchingNextPage}
              onClick={() => history.fetchNextPage()}
            >
              {history.isFetchingNextPage ? "Loading…" : "Load more"}
            </button>
          )}
        </div>
      )}
      {history.isFetchNextPageError && (
        <p role="alert" className={`alert-error ${styles.footError}`}>
          Could not load more runs. Try Load more again.
        </p>
      )}
    </section>
  );
}

const GROUPS = [
  { key: "downloaded", label: "Downloaded", colour: "var(--ok)" },
  { key: "failed", label: "Failed", colour: "var(--bad)" },
  { key: "refused", label: "Refused by Audible", colour: "var(--violet)" },
] as const;

function RunBooks({ runId }: { runId: number }) {
  const books = useRunBooks(runId);
  if (books.isPending) {
    return (
      <div className={styles.groups} role="status" aria-busy="true" aria-label="Loading the run's books">
        {GROUPS.map((group) => (
          <Skeleton key={group.key} width="60%" />
        ))}
      </div>
    );
  }
  if (books.isError) {
    return (
      <ErrorCard
        title={`Could not load the books of run ${runId}`}
        error={books.error}
        onRetry={() => books.refetch()}
      />
    );
  }
  if (!books.data.recorded) {
    return <p className={styles.unrecorded}>This run is from before Audible Sync recorded each book a run took on.</p>;
  }
  return (
    <div className={styles.groups}>
      {GROUPS.map((group) => {
        const list = books.data[group.key];
        return (
          <div key={group.key} className={styles.group}>
            <h3 className={styles.groupTitle} style={{ color: group.colour }}>
              <span className={styles.dot} style={{ background: group.colour }} />
              {group.label} ({list.length})
            </h3>
            {list.length === 0 ? (
              <span className={styles.none}>None</span>
            ) : (
              <ul className={styles.books}>
                {list.map((book, index) => (
                  // A book can appear twice in one run (failed, then tried again)
                  // biome-ignore lint/suspicious/noArrayIndexKey: the order is the run's and never changes
                  <li key={`${book.asin}-${index}`}>
                    <RunBookLine book={book} outcome={group.key} />
                  </li>
                ))}
              </ul>
            )}
          </div>
        );
      })}
    </div>
  );
}

function RunBookLine({ book, outcome }: { book: RunBook; outcome: "downloaded" | "failed" | "refused" }) {
  const content = (
    <>
      {book.book_id != null ? (
        <Cover bookId={book.book_id} size={28} className={styles.cover} />
      ) : (
        <span className={styles.cover} />
      )}
      <span className={styles.bookText}>
        <span className={styles.bookTitle}>{book.title ?? book.asin}</span>
        <span className={styles.bookNote}>{runBookNote(book, outcome)}</span>
      </span>
    </>
  );
  // A book that has left the library has no page to go to
  return book.book_id != null ? (
    <Link to={`/library/${book.book_id}`} className={styles.book}>
      {content}
    </Link>
  ) : (
    <span className={styles.book}>{content}</span>
  );
}

/**
 * The service log, oldest first. Follow refetches every 2 s and keeps the newest line
 * in view; scrolling up to read turns it off, so lines stop moving under the reader.
 */
function ServiceLog() {
  const [level, setLevel] = useState<Level>("INFO");
  const [follow, setFollow] = useState(true);
  const logs = useLogs(level, follow);
  const box = useRef<HTMLDivElement>(null);
  const items = logs.data?.items;

  // After the lines change (and before they are painted), keep the newest in view
  useLayoutEffect(() => {
    const element = box.current;
    if (follow && element && items) element.scrollTop = element.scrollHeight;
  }, [items, follow]);

  const scrolled = () => {
    const element = box.current;
    if (!element || !follow) return;
    const atBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 24;
    if (!atBottom) setFollow(false);
  };

  return (
    <section aria-labelledby="logs-h" className={styles.card}>
      <div className={styles.logHead}>
        <h2 id="logs-h" className={styles.logTitle}>
          Service log
        </h2>
        <span className={styles.hint}>Newest 1000 lines kept in memory</span>
        <fieldset className={styles.levels}>
          <legend className="visually-hidden">Minimum level</legend>
          {LEVELS.map((name) => (
            <button key={name} type="button" aria-pressed={name === level} onClick={() => setLevel(name)}>
              {name[0]}
              {name.slice(1).toLowerCase()}
            </button>
          ))}
        </fieldset>
        <label className="checkbox">
          <input type="checkbox" checked={follow} onChange={(event) => setFollow(event.target.checked)} />
          Follow
        </label>
      </div>
      {logs.isError ? (
        <div className={styles.logError}>
          <ErrorCard title="Could not load the service log" error={logs.error} onRetry={() => logs.refetch()} />
        </div>
      ) : (
        // biome-ignore lint/a11y/noNoninteractiveTabindex: the log scrolls, so the keyboard must be able to reach it
        <div ref={box} role="log" aria-label="Log lines" tabIndex={0} className={styles.log} onScroll={scrolled}>
          {items === undefined ? (
            <Skeleton width="50%" />
          ) : items.length === 0 ? (
            <span className={styles.hint}>No log lines at this level yet.</span>
          ) : (
            items.map((line, index) => (
              // biome-ignore lint/suspicious/noArrayIndexKey: log lines have no id and hold no state, so reuse by position is harmless
              <div key={index} className={styles.line}>
                <time dateTime={line.time} title={new Date(line.time).toLocaleString()} className={styles.time}>
                  {logTime(line.time)}
                </time>
                <span className={styles.level} style={{ color: LEVEL_COLOUR[line.level] ?? "var(--text)" }}>
                  {line.level}
                </span>
                <span className={styles.logger}>{line.logger}</span>
                <span className={styles.message}>{line.message}</span>
              </div>
            ))
          )}
        </div>
      )}
    </section>
  );
}
