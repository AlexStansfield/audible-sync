/**
 * A book's panel (DESIGN.md, "A book's panel"): from the right over the dimmed page,
 * the whole screen on a phone, opened from a cover or a title and named in the URL
 * (`?book=`), so Back closes it. The row's data shows at once; the description
 * follows from `GET /api/catalog/books/{asin}`.
 */
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { ErrorCard } from "../components/ErrorCard";
import { Icon } from "../components/Icon";
import { Skeleton } from "../components/Skeleton";
import { BOOK_STATUS, type BookStatus, formatDate, formatLength } from "../lib/format";
import { Byline, type CatalogContext, CatalogCover, Markers, PriceBlock, WishlistButton } from "./BookRow";
import styles from "./Catalog.module.css";
import { useCatalogBook } from "./queries";
import { type CatalogBook, seriesLine } from "./text";

const LANGUAGES: Record<string, string> = {
  en: "English",
  de: "German",
  fr: "French",
  es: "Spanish",
  it: "Italian",
  ja: "Japanese",
  pt: "Portuguese",
  nl: "Dutch",
};

export function BookPanel({
  asin,
  shown,
  accountId,
  marketplace,
  context,
  ready = true,
  onClose,
}: {
  asin: string;
  /** The book as the list had it, shown while the detail loads */
  shown: CatalogBook | undefined;
  accountId: number | null;
  marketplace?: string;
  context: CatalogContext;
  /** Whether the account to ask through is known yet; until then nothing is asked */
  ready?: boolean;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const detail = useCatalogBook(asin, accountId, ready);
  const book: CatalogBook | undefined = detail.data ?? shown;

  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal?.();
    return () => dialog?.close?.();
  }, []);

  return (
    // biome-ignore lint/a11y/useKeyWithClickEvents: the backdrop click; Escape (onCancel) is the keyboard equivalent
    <dialog
      ref={ref}
      className={styles.panel}
      aria-label={book?.title ?? "Book"}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target === ref.current) onClose();
      }}
    >
      <div className={styles.panelBody}>
        <div className={styles.panelHead}>
          {marketplace && <span className={styles.eyebrow}>{marketplace}</span>}
          <button type="button" className={`icon-btn ${styles.panelClose}`} aria-label="Close" onClick={onClose}>
            <Icon name="close" size={20} strokeWidth={2.2} />
          </button>
        </div>
        {!book ? (
          detail.isError ? (
            <ErrorCard title="Could not load the book" error={detail.error} onRetry={() => void detail.refetch()} />
          ) : (
            <Skeleton width="100%" height={160} />
          )
        ) : (
          <>
            <CatalogCover book={book} size={160} />
            <div className={styles.panelTitleBlock}>
              <h2 className={styles.panelTitle}>{book.title}</h2>
              {book.subtitle && <p className={styles.panelSubtitle}>{book.subtitle}</p>}
              <Byline book={book} />
              {seriesLine(book) && <p className={styles.series}>{seriesLine(book)}</p>}
              <Markers book={book} accounts={context.accounts} hideLibrary />
            </div>
            {book.library.map((copy) => {
              const account = context.accounts.find((one) => one.id === copy.account_id);
              const status = copy.status ? BOOK_STATUS[copy.status as BookStatus]?.label : null;
              return (
                <Link key={copy.book_id} to={`/library/${copy.book_id}`} className={styles.heldCard}>
                  <Icon name="check" size={16} strokeWidth={2.6} />
                  <span>
                    <strong>In your library</strong>
                    <span className={styles.heldWhere}>
                      {[context.accounts.length > 1 ? account?.name : null, status].filter(Boolean).join(", ")}
                    </span>
                  </span>
                  <span className={styles.open}>Open →</span>
                </Link>
              );
            })}
            <div className={styles.panelPrice}>
              <PriceBlock book={book} large />
              <WishlistButton book={book} context={context} />
            </div>
            <Details book={book} />
            {detail.isError ? (
              <p className={styles.detailError} role="alert">
                Could not load the description.{" "}
                <button type="button" className="link-btn" onClick={() => void detail.refetch()}>
                  Try again
                </button>
              </p>
            ) : (
              <Description html={detail.data?.description_html} loading={detail.isPending} />
            )}
          </>
        )}
      </div>
    </dialog>
  );
}

function Details({ book }: { book: CatalogBook }) {
  const rows: [string, string][] = [
    ["Length", formatLength(book.length_seconds == null ? null : Math.round(book.length_seconds / 60))],
    ["Released", book.release_date ? formatDate(book.release_date) : ""],
    ["Language", book.language ? (LANGUAGES[book.language] ?? book.language) : ""],
    ["Publisher", book.publisher ?? ""],
    ["Format", book.abridged == null ? "" : book.abridged ? "Abridged" : "Unabridged"],
    ["Genres", book.genres.join(", ")],
    [
      "Rating",
      book.rating != null && book.rating_count
        ? `★ ${book.rating.toFixed(1)} from ${book.rating_count.toLocaleString()} ratings`
        : "",
    ],
    ["ASIN", book.asin],
  ];
  return (
    <dl className={styles.details}>
      {rows
        .filter(([, value]) => value)
        .map(([label, value]) => (
          <div key={label} className={styles.detailRow}>
            <dt>{label}</dt>
            <dd className={label === "ASIN" ? "mono" : undefined}>{value}</dd>
          </div>
        ))}
    </dl>
  );
}

/** The description, folded to about six lines with Show more. */
function Description({ html, loading }: { html: string | undefined; loading: boolean }) {
  const [open, setOpen] = useState(false);
  if (loading) return <Skeleton width="100%" height={80} />;
  if (!html) return null;
  return (
    <section className={styles.description} aria-label="Description">
      <div
        className={`${styles.descriptionText} ${open ? "" : styles.folded}`}
        // biome-ignore lint/security/noDangerouslySetInnerHtml: not Audible's markup but HTML the service wrote afresh from an allow-list (`safe_html`), as on the Book page
        dangerouslySetInnerHTML={{ __html: html }}
      />
      <button type="button" className="link-btn" onClick={() => setOpen(!open)}>
        {open ? "Show less" : "Show more"}
      </button>
    </section>
  );
}
