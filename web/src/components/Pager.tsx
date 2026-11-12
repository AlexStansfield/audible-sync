import { pageList } from "../lib/paging";
import styles from "./Pager.module.css";

/** The range ("1–24 of 354"), then Prev, the page numbers with gaps, and Next. */
export function Pager({
  page,
  pages,
  label,
  onPage,
}: {
  page: number;
  pages: number;
  label: string;
  onPage: (page: number) => void;
}) {
  return (
    <nav aria-label="Pages" className={styles.pager}>
      <span>{label}</span>
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
