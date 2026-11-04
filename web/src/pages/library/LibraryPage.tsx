/**
 * The Library (Library.dc.html): every book, filtered by what the URL says, as a grid of
 * covers or a table, with monitoring toggled per book and the bulk actions in select
 * mode (`bulk`). An empty library and a filter that matches nothing each get a card of
 * their own (`empty`).
 */
import { type CSSProperties, useEffect, useReducer, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { useRefreshDuringRun, useStatus } from "../../api/queries";
import type { components } from "../../api/schema";
import { usePendingIds } from "../../api/usePendingIds";
import { useRetryUnavailable } from "../../api/useRetryUnavailable";
import { ConfirmDialog } from "../../components/Dialog";
import { ErrorCard } from "../../components/ErrorCard";
import { Icon } from "../../components/Icon";
import { Skeleton } from "../../components/Skeleton";
import { useToast } from "../../components/Toasts";
import { BOOK_STATUS, plural } from "../../lib/format";
import { useStats } from "../dashboard/queries";
import { BookGrid, type BooksProps, BookTable, GridSkeleton } from "./Books";
import styles from "./Library.module.css";
import {
  bulkFilter,
  chipCounts,
  DEFAULT_QUERY,
  defaultOrder,
  type LibraryQuery,
  MAX_SEARCH,
  PAGE_SIZE,
  pageList,
  parse,
  rangeText,
  SEARCH_DELAY,
  SORTS,
  type Sort,
  STATUSES,
  serialise,
  VIEW_KEY,
} from "./libraryQuery";
import { liveBook } from "./live";
import { MONITOR_KEY, useBulkAction, useLibrary, useMonitorBook } from "./queries";
import { EMPTY, isSelected, pageSelected, selectedCount, selectionReducer } from "./selection";
import { bulkResultText, librarySubtitle, noResultsText, selectionLabel } from "./text";

type BulkAction = components["schemas"]["BulkAction"];
type BulkResult = components["schemas"]["BulkResultOut"];
type BookCounts = components["schemas"]["BookCounts"];

/** What a run in flight changes here, refetched as it takes up each book. */
const DURING_RUN = [["stats"], ["books", "list"]] as const;

type View = "grid" | "table";

function storedView(): View {
  try {
    return localStorage.getItem(VIEW_KEY) === "table" ? "table" : "grid";
  } catch {
    return "grid";
  }
}

export function LibraryPage() {
  const [params, setParams] = useSearchParams();
  const query = parse(params);
  const status = useStatus();
  const stats = useStats();
  const list = useLibrary(query);
  const monitor = useMonitorBook(query);
  const pendingMonitors = usePendingIds(MONITOR_KEY);
  const bulk = useBulkAction();
  const toast = useToast();
  const top = useRef<HTMLHeadingElement>(null);

  const [view, setView] = useState<View>(storedView);
  const [selecting, setSelecting] = useState(false);
  const [selection, dispatch] = useReducer(selectionReducer, EMPTY);
  const [result, setResult] = useState<{ action: BulkAction; result: BulkResult } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  useRefreshDuringRun(status.data, DURING_RUN);

  /** A new view of the library: back to page 1 unless the change is the page. */
  const update = (changes: Partial<LibraryQuery>, options: { replace?: boolean } = {}) => {
    setParams(serialise({ ...query, page: 1, ...changes }), { replace: options.replace });
  };

  // Books picked under other filters are no longer on show: an action must not reach them
  const filterKey = JSON.stringify(bulkFilter(query));
  const lastFilter = useRef(filterKey);
  useEffect(() => {
    if (lastFilter.current === filterKey) return;
    lastFilter.current = filterKey;
    dispatch({ type: "filters" });
  }, [filterKey]);

  // A page past the end (books deleted, or a stale bookmark): the last page there is
  const pages = list.data ? Math.max(1, Math.ceil(list.data.total / PAGE_SIZE)) : 1;
  const pastEnd = list.data !== undefined && !list.isPlaceholderData && query.page > pages;
  useEffect(() => {
    if (pastEnd) setParams(serialise({ ...query, page: pages }), { replace: true });
  });

  const accounts = status.data?.accounts ?? [];
  const codes =
    accounts.length > 1 ? new Map(accounts.map((account) => [account.id, account.country_code.toUpperCase()])) : null;
  const account = accounts.find((one) => one.id === query.accountId);
  const counts: BookCounts | undefined =
    query.accountId === null
      ? stats.data?.books
      : stats.data?.accounts.find((one) => one.account.id === query.accountId)?.books;
  const libraryTotal = stats.data?.books.total;
  const noBooks = libraryTotal === 0;
  // Only what the retry would queue: titles Audible withdrew come back with the sync
  const retryable = counts?.retryable_unavailable ?? 0;

  const run = status.data?.current_run;
  const live = liveBook(run);

  const books = list.data?.items ?? [];
  const pageIds = books.map((book) => book.id);
  const total = list.data?.total ?? 0;
  const count = selectedCount(selection, total);

  const stopSelecting = () => {
    setSelecting(false);
    dispatch({ type: "clear" });
  };

  const runBulk = (action: BulkAction) => {
    const body = selection.all
      ? { action, filter: bulkFilter(query) }
      : { action, ids: [...selection.ids].sort((a, b) => a - b) };
    bulk.mutate(body, {
      onSuccess: (outcome) => {
        setResult({ action, result: outcome });
        setConfirmDelete(false);
        stopSelecting();
      },
      onError: (error) => {
        setConfirmDelete(false);
        toast.error("Could not change the selected books", error);
      },
    });
  };

  const changeView = (next: View) => {
    setView(next);
    try {
      localStorage.setItem(VIEW_KEY, next);
    } catch {
      // A browser that will not store it just forgets the choice
    }
  };

  const goToPage = (page: number) => {
    setParams(serialise({ ...query, page }));
    top.current?.scrollIntoView?.({ block: "start" });
  };

  const booksProps: BooksProps = {
    books,
    live,
    codes,
    selecting,
    isSelected: (id) => isSelected(selection, id),
    onToggleSelect: (id) => dispatch({ type: "toggle", id, pageIds }),
    pageSelected: pageSelected(selection, pageIds),
    onSelectPage: (on) => dispatch({ type: "page", ids: pageIds, on }),
    onMonitor: (book, monitored) => monitor.mutate({ id: book.id, monitored }),
    monitorDisabled: (id) => list.isPlaceholderData || pendingMonitors.has(id),
  };

  const noResults = list.data !== undefined && total === 0 && !noBooks;

  return (
    <>
      <header className="page-header">
        <div className={styles.titleBlock}>
          <h1 ref={top} className="page-title">
            Library
          </h1>
          <span className="page-subtitle">
            {libraryTotal === undefined ? (
              <Skeleton width={180} />
            ) : (
              librarySubtitle(libraryTotal, stats.data?.accounts.length ?? 0)
            )}
          </span>
        </div>
        {!noBooks && libraryTotal !== undefined && (
          <div className={styles.headerActions}>
            <RetryUnavailableButton count={retryable} accountId={query.accountId} />
            <button
              type="button"
              className={`btn btn-secondary ${selecting ? styles.selectOn : ""}`}
              aria-pressed={selecting}
              onClick={() => (selecting ? stopSelecting() : setSelecting(true))}
            >
              <Icon name="select" size={16} strokeWidth={2.2} />
              {selecting ? "Done" : "Select"}
            </button>
          </div>
        )}
      </header>

      {noBooks ? (
        <EmptyLibrary />
      ) : (
        <>
          <Filters query={query} update={update} counts={counts} accounts={accounts} view={view} onView={changeView} />

          {result && <ResultBanner {...result} onDismiss={() => setResult(null)} />}

          {selecting && (
            <section aria-label="Selection" className={styles.selectionBar}>
              <div className={styles.selectionInfo}>
                <span className={styles.strong}>{selectionLabel(count, selection.all)}</span>
                {!selection.all && books.length > 0 && (
                  <>
                    <button
                      type="button"
                      className="link-btn"
                      onClick={() => dispatch({ type: "page", ids: pageIds, on: true })}
                    >
                      Select all on this page ({books.length})
                    </button>
                    {total > books.length && (
                      <button type="button" className="link-btn" onClick={() => dispatch({ type: "all" })}>
                        Select all {total.toLocaleString()} matching these filters
                      </button>
                    )}
                  </>
                )}
                {count > 0 && (
                  <button type="button" className={styles.clear} onClick={() => dispatch({ type: "clear" })}>
                    Clear
                  </button>
                )}
              </div>
              <div className={styles.selectionActions}>
                {(
                  [
                    ["monitor", "Monitor"],
                    ["unmonitor", "Unmonitor"],
                    ["retry", "Retry"],
                  ] as const
                ).map(([action, label]) => (
                  <button
                    key={action}
                    type="button"
                    className="btn btn-secondary btn-small"
                    disabled={count === 0 || bulk.isPending}
                    onClick={() => runBulk(action)}
                  >
                    {label}
                  </button>
                ))}
                <button
                  type="button"
                  className="btn btn-danger-outline btn-small"
                  disabled={count === 0 || bulk.isPending}
                  onClick={() => setConfirmDelete(true)}
                >
                  Delete files
                </button>
              </div>
            </section>
          )}

          {list.isError && !list.data ? (
            <ErrorCard title="Could not load the library" error={list.error} onRetry={() => void list.refetch()} />
          ) : list.data === undefined ? (
            <GridSkeleton />
          ) : noResults ? (
            <section aria-labelledby="nores-h" className={styles.emptyCard}>
              <span className={styles.emptyIcon}>
                <Icon name="searchEmpty" size={26} />
              </span>
              <h2 id="nores-h" className={styles.emptyTitle}>
                No books match these filters
              </h2>
              <p className={styles.emptyText}>{noResultsText(query, codes ? account?.name : undefined)}</p>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => setParams(serialise({ ...DEFAULT_QUERY, sort: query.sort, order: query.order }))}
              >
                Clear filters
              </button>
            </section>
          ) : (
            <>
              {view === "grid" ? <BookGrid {...booksProps} /> : <BookTable {...booksProps} />}
              <Pager page={Math.min(query.page, pages)} pages={pages} total={total} onPage={goToPage} />
            </>
          )}
        </>
      )}

      <ConfirmDialog
        open={confirmDelete}
        title={`Delete the files for ${plural(count, "book")}?`}
        confirmLabel={`Delete files for ${plural(count, "book")}`}
        cancelLabel="Keep files"
        busy={bulk.isPending}
        onConfirm={() => runBulk("delete_files")}
        onCancel={() => setConfirmDelete(false)}
      >
        <p className={styles.dialogText}>
          The audio, covers and annotations are removed from your audiobooks folder, with any folders left empty. The
          books are unmonitored so they will not download again, and they stay in your library. Books with no files, or
          that a run is downloading, are skipped.
        </p>
      </ConfirmDialog>
    </>
  );
}

function RetryUnavailableButton({ count, accountId }: { count: number; accountId: number | null }) {
  const retry = useRetryUnavailable();
  if (count === 0) return null;
  return (
    <button
      type="button"
      className="btn btn-secondary"
      disabled={retry.isPending}
      onClick={() => retry.mutate(accountId ?? undefined)}
    >
      Retry unavailable ({count.toLocaleString()})
    </button>
  );
}

function EmptyLibrary() {
  return (
    <section aria-labelledby="empty-h" className={`${styles.emptyCard} ${styles.dashed}`}>
      <span className={styles.emptyIconLarge}>
        <Icon name="library" size={30} strokeWidth={1.8} />
      </span>
      <h2 id="empty-h" className={styles.emptyTitle}>
        Your library is empty
      </h2>
      <p className={styles.emptyText}>
        Add an Audible account to fetch your library. The books appear here after its first sync.
      </p>
      <Link to="/accounts" className="btn btn-primary">
        <Icon name="plus" size={16} strokeWidth={2.6} />
        Add an Audible account
      </Link>
    </section>
  );
}

function Filters({
  query,
  update,
  counts,
  accounts,
  view,
  onView,
}: {
  query: LibraryQuery;
  update: (changes: Partial<LibraryQuery>, options?: { replace?: boolean }) => void;
  counts: BookCounts | undefined;
  accounts: components["schemas"]["AccountOut"][];
  view: View;
  onView: (view: View) => void;
}) {
  const shown = counts && chipCounts(counts, query.monitored);
  const chips = [
    { status: null, label: "All", colour: "var(--text)", count: shown?.total },
    ...STATUSES.map((status) => ({
      status,
      label: BOOK_STATUS[status].label,
      colour: BOOK_STATUS[status].colour,
      count: shown?.byStatus[status],
    })),
  ];
  return (
    <section aria-label="Filters" className={styles.filters}>
      <div className={styles.toolbar}>
        <SearchBox value={query.q} onSearch={(q) => update({ q }, { replace: true })} />
        {accounts.length > 1 && (
          <label className={styles.selectLabel}>
            Account
            <select
              className={styles.select}
              value={query.accountId ?? ""}
              onChange={(event) => update({ accountId: event.target.value ? Number(event.target.value) : null })}
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
        <label className={styles.selectLabel}>
          Sort
          <select
            className={styles.select}
            value={query.sort}
            onChange={(event) => {
              const sort = event.target.value as Sort;
              update({ sort, order: defaultOrder(sort) });
            }}
          >
            {SORTS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <fieldset className={styles.viewSwitch}>
          <legend className="visually-hidden">View</legend>
          <button
            type="button"
            aria-label="Grid view"
            aria-pressed={view === "grid"}
            className={styles.viewButton}
            onClick={() => onView("grid")}
          >
            <Icon name="grid" size={16} />
          </button>
          <button
            type="button"
            aria-label="Table view"
            aria-pressed={view === "table"}
            className={styles.viewButton}
            onClick={() => onView("table")}
          >
            <Icon name="list" size={16} />
          </button>
        </fieldset>
      </div>
      <fieldset className={styles.chips}>
        <legend className="visually-hidden">Status</legend>
        {chips.map((chip) => (
          <button
            key={chip.label}
            type="button"
            aria-pressed={query.status === chip.status}
            className={styles.chip}
            style={{ "--chip": chip.colour } as CSSProperties}
            onClick={() => update({ status: chip.status })}
          >
            <span className={styles.dot} />
            {chip.label}
            <span className={styles.chipCount}>{chip.count?.toLocaleString() ?? "–"}</span>
          </button>
        ))}
        <span className={styles.divider} aria-hidden="true" />
        {query.monitored === true ? (
          // The Dashboard's "+N more waiting" asks for monitored books only
          <label className="checkbox">
            <input type="checkbox" checked onChange={() => update({ monitored: null })} />
            Monitored only
          </label>
        ) : (
          <label className="checkbox">
            <input
              type="checkbox"
              checked={query.monitored === false}
              onChange={(event) => update({ monitored: event.target.checked ? false : null })}
            />
            Unmonitored only
          </label>
        )}
      </fieldset>
    </section>
  );
}

/**
 * The search box: the URL follows what is typed once typing pauses, and the box follows
 * the URL when it changes some other way (Clear filters, the back button).
 */
function SearchBox({ value, onSearch }: { value: string; onSearch: (q: string) => void }) {
  const [text, setText] = useState(value);
  const typed = useRef(text);
  typed.current = text;
  const search = useRef(onSearch);
  search.current = onSearch;

  useEffect(() => {
    // A trailing space being typed is not a different search
    if (value !== typed.current.trim()) setText(value);
  }, [value]);

  useEffect(() => {
    const wanted = text.trim();
    if (wanted === value) return;
    const timer = setTimeout(() => search.current(wanted), SEARCH_DELAY);
    return () => clearTimeout(timer);
  }, [text, value]);

  return (
    <div className={styles.search}>
      <Icon name="search" size={16} className={styles.searchIcon} />
      <label htmlFor="library-q" className="visually-hidden">
        Search the library
      </label>
      <input
        id="library-q"
        type="search"
        className={`input ${styles.searchInput}`}
        placeholder="Search title, author or series"
        maxLength={MAX_SEARCH}
        value={text}
        onChange={(event) => setText(event.target.value)}
      />
    </div>
  );
}

function ResultBanner({
  action,
  result,
  onDismiss,
}: {
  action: BulkAction;
  result: BulkResult;
  onDismiss: () => void;
}) {
  const { headline, detail } = bulkResultText(action, result);
  return (
    <div role="status" className={styles.result}>
      <Icon name="check" size={18} strokeWidth={2.6} className={styles.resultIcon} />
      <span className={styles.resultText}>
        <strong>{headline}</strong>
        {detail && <span className={styles.secondary}> {detail}</span>}
      </span>
      <button type="button" className="icon-btn" aria-label="Dismiss" onClick={onDismiss}>
        <Icon name="close" size={16} strokeWidth={2.2} />
      </button>
    </div>
  );
}

function Pager({
  page,
  pages,
  total,
  onPage,
}: {
  page: number;
  pages: number;
  total: number;
  onPage: (page: number) => void;
}) {
  return (
    <nav aria-label="Pages" className={styles.pager}>
      <span>{rangeText(page, PAGE_SIZE, total)}</span>
      {pages > 1 && (
        <div className={styles.pageButtons}>
          <button type="button" className={styles.pageButton} disabled={page <= 1} onClick={() => onPage(page - 1)}>
            Prev
          </button>
          {pageList(page, pages).map((number, index) =>
            number === null ? (
              // biome-ignore lint/suspicious/noArrayIndexKey: a gap has no identity of its own
              <span key={`gap-${index}`} className={styles.gap}>
                …
              </span>
            ) : (
              <button
                key={number}
                type="button"
                className={styles.pageButton}
                aria-current={number === page ? "page" : undefined}
                aria-label={`Page ${number}`}
                onClick={() => onPage(number)}
              >
                {number}
              </button>
            ),
          )}
          <button type="button" className={styles.pageButton} disabled={page >= pages} onClick={() => onPage(page + 1)}>
            Next
          </button>
        </div>
      )}
    </nav>
  );
}
