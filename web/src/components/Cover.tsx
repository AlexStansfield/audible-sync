import { useState } from "react";
import styles from "./Cover.module.css";
import { Icon } from "./Icon";

/**
 * A book's square cover from `api/books/{id}/cover` - a relative URL, so it follows
 * the page's base path - loaded lazily, with a plain placeholder when there is none.
 * Decorative: the title beside it names the book.
 */
export function Cover({ bookId, size, className }: { bookId: number; size?: number; className?: string }) {
  // Which book's cover failed, not just whether one did: the same Cover shown for the
  // next book (the run card) must try that book's
  const [failedId, setFailedId] = useState<number | null>(null);
  const failed = failedId === bookId;
  const style = size ? { width: size, height: size } : undefined;
  if (failed) {
    return (
      <span className={`${styles.cover} ${styles.placeholder} ${className ?? ""}`} style={style}>
        <Icon name="book" size={size ? Math.max(14, size / 3) : 28} />
      </span>
    );
  }
  return (
    <img
      src={`api/books/${bookId}/cover`}
      alt=""
      loading="lazy"
      decoding="async"
      className={`${styles.cover} ${className ?? ""}`}
      style={style}
      onError={() => setFailedId(bookId)}
    />
  );
}
