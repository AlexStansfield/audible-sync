/**
 * The books on one page of the Library, as a grid of covers or as a table. Both show
 * the book a run is downloading as such, with its progress, whatever the listing last
 * read; and both toggle monitoring and, in select mode, the selection.
 */
import type { CSSProperties } from "react";
import { Link } from "react-router";
import type { components } from "../../api/schema";
import { Cover } from "../../components/Cover";
import { Icon } from "../../components/Icon";
import { Skeleton } from "../../components/Skeleton";
import { Switch } from "../../components/Switch";
import { BOOK_STATUS, type BookStatus, formatDay } from "../../lib/format";
import styles from "./Library.module.css";
import { seriesText } from "./text";

type Book = components["schemas"]["BookOut"];

export type BooksProps = {
  books: Book[];
  /** The book a run is downloading, and how far it is (0-1, `null` when the size is unknown) */
  live: { id: number; fraction: number | null } | null;
  /** Account id → `UK`, given only when there is more than one account */
  codes: Map<number, string> | null;
  selecting: boolean;
  isSelected: (id: number) => boolean;
  onToggleSelect: (id: number) => void;
  pageSelected: boolean;
  onSelectPage: (on: boolean) => void;
  onMonitor: (book: Book, monitored: boolean) => void;
  /**
   * Whether a book's monitoring control waits: the previous page is on show while the
   * next loads (a change could not be shown), or a change to it is still in flight
   */
  monitorDisabled: (id: number) => boolean;
};

function statusOf(book: Book, live: BooksProps["live"]): { label: string; colour: string } {
  const status = (book.id === live?.id ? "downloading" : book.status) as BookStatus | null;
  const known = status ? BOOK_STATUS[status] : undefined;
  return known ?? { label: book.status ?? "Unknown", colour: "var(--muted)" };
}

function MonitorButton({
  book,
  onMonitor,
  disabled,
}: {
  book: Book;
  onMonitor: BooksProps["onMonitor"];
  disabled: boolean;
}) {
  return (
    <button
      type="button"
      className={`${styles.bookmark} ${book.monitored ? styles.bookmarkOn : ""}`}
      aria-label={book.monitored ? `Stop monitoring ${book.title}` : `Monitor ${book.title}`}
      aria-pressed={book.monitored}
      disabled={disabled}
      onClick={() => onMonitor(book, !book.monitored)}
    >
      <Icon name="bookmark" size={16} filled={book.monitored} />
    </button>
  );
}

export function BookGrid({
  books,
  live,
  selecting,
  isSelected,
  onToggleSelect,
  onMonitor,
  monitorDisabled,
}: BooksProps) {
  return (
    <section aria-label="Books" className={styles.grid}>
      {books.map((book) => {
        const status = statusOf(book, live);
        const selected = selecting && isSelected(book.id);
        const progress = book.id === live?.id ? live.fraction : undefined;
        return (
          <div key={book.id} className={`${styles.tile} ${book.monitored ? "" : styles.unmonitored}`}>
            <div className={`${styles.tileCover} ${selected ? styles.selectedCover : ""}`}>
              <Link to={`/library/${book.id}`} className={styles.coverLink} aria-label={book.title}>
                <Cover bookId={book.id} className={styles.coverImage} />
                <span className={styles.badge} style={{ "--chip": status.colour } as CSSProperties}>
                  <span className={styles.dot} />
                  {status.label}
                </span>
                {progress !== undefined && (
                  <span className={styles.strip} aria-hidden="true">
                    <span
                      className={progress === null ? styles.stripUnknown : styles.stripFill}
                      style={progress === null ? undefined : { width: `${Math.round(progress * 100)}%` }}
                    />
                  </span>
                )}
              </Link>
              {selecting && (
                <label className={styles.tileCheck}>
                  <input
                    type="checkbox"
                    checked={selected}
                    onChange={() => onToggleSelect(book.id)}
                    aria-label={`Select ${book.title}`}
                  />
                </label>
              )}
            </div>
            <div className={styles.tileFoot}>
              <div className={styles.tileText}>
                {/* The cover's link already names the book; this one is for the pointer */}
                <Link to={`/library/${book.id}`} className={styles.tileTitle} tabIndex={-1} aria-hidden="true">
                  {book.title}
                </Link>
                <span className={styles.tileAuthor}>{book.authors.join(", ") || "Unknown author"}</span>
              </div>
              <MonitorButton book={book} onMonitor={onMonitor} disabled={monitorDisabled(book.id)} />
            </div>
          </div>
        );
      })}
    </section>
  );
}

export function BookTable({
  books,
  live,
  codes,
  selecting,
  isSelected,
  onToggleSelect,
  pageSelected,
  onSelectPage,
  onMonitor,
  monitorDisabled,
}: BooksProps) {
  return (
    <section aria-label="Books" className={styles.tableCard}>
      <table className={styles.table}>
        <thead>
          <tr>
            {selecting && (
              <th scope="col" className={styles.checkCell}>
                <input
                  type="checkbox"
                  checked={pageSelected}
                  onChange={(event) => onSelectPage(event.target.checked)}
                  aria-label="Select all on this page"
                />
              </th>
            )}
            <th scope="col">Title</th>
            <th scope="col">Author</th>
            <th scope="col">Series</th>
            {codes && <th scope="col">Account</th>}
            <th scope="col">Status</th>
            <th scope="col">Added</th>
            <th scope="col" className={styles.right}>
              Monitored
            </th>
          </tr>
        </thead>
        <tbody>
          {books.map((book) => {
            const status = statusOf(book, live);
            const selected = selecting && isSelected(book.id);
            return (
              <tr key={book.id} className={selected ? styles.selectedRow : undefined}>
                {selecting && (
                  <td className={styles.checkCell}>
                    <input
                      type="checkbox"
                      checked={selected}
                      onChange={() => onToggleSelect(book.id)}
                      aria-label={`Select ${book.title}`}
                    />
                  </td>
                )}
                <td className={book.monitored ? undefined : styles.unmonitored}>
                  <Link to={`/library/${book.id}`} className={styles.titleLink}>
                    <Cover bookId={book.id} size={36} />
                    <span>{book.title}</span>
                  </Link>
                </td>
                <td className={styles.secondary}>{book.authors.join(", ")}</td>
                <td className={styles.secondary}>{seriesText(book) ?? "—"}</td>
                {codes && (
                  <td>
                    <span className={styles.cc}>{codes.get(book.account_id) ?? "?"}</span>
                  </td>
                )}
                <td>
                  <span className={styles.status} style={{ "--chip": status.colour } as CSSProperties}>
                    <span className={styles.dot} />
                    {status.label}
                  </span>
                </td>
                <td className={styles.muted}>{book.date_added ? formatDay(book.date_added) : "—"}</td>
                <td className={styles.right}>
                  <Switch
                    checked={book.monitored}
                    label={`Monitor ${book.title}`}
                    disabled={monitorDisabled(book.id)}
                    onChange={(monitored) => onMonitor(book, monitored)}
                  />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}

/** Covers in the shape of the grid while the first page loads. */
export function GridSkeleton({ count = 12 }: { count?: number }) {
  return (
    <section aria-label="Loading the library" aria-busy="true" className={styles.grid}>
      {Array.from({ length: count }, (_, i) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: identical placeholders
        <div key={i} className={styles.tile}>
          <Skeleton height={0} style={{ width: "100%", aspectRatio: "1", height: "auto", borderRadius: 8 }} />
          <Skeleton width="80%" />
          <Skeleton width="50%" height={10} />
        </div>
      ))}
    </section>
  );
}
