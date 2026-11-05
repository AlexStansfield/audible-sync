import { ApiError, NetworkError } from "../api/client";
import styles from "./ErrorCard.module.css";
import { Icon } from "./Icon";

/** The request and status in small mono: `GET /api/sync/runs · 500`. */
export function requestLine(error: unknown): string | null {
  if (error instanceof ApiError) {
    return `${error.method} ${error.path} · ${error.status}`.trim();
  }
  if (error instanceof NetworkError) {
    return `${error.method} ${error.path} · no response`;
  }
  return null;
}

export function errorDetail(error: unknown): string {
  if (error instanceof ApiError) return error.detail;
  if (error instanceof Error) return error.message;
  return String(error);
}

/**
 * A card that failed to load, in place of the card: what could not load, the API's
 * `detail`, the request, and Retry. The rest of the screen keeps working.
 */
export function ErrorCard({ title, error, onRetry }: { title: string; error: unknown; onRetry?: () => void }) {
  const request = requestLine(error);
  return (
    <div role="alert" className={`card ${styles.card}`}>
      <div className={styles.body}>
        <Icon name="warning" size={20} className={styles.icon} />
        <div className={styles.text}>
          <span className={styles.title}>{title}</span>
          <span className={styles.detail}>{errorDetail(error)}</span>
          {request && <span className={styles.request}>{request}</span>}
        </div>
      </div>
      {onRetry && (
        <button type="button" className="btn btn-secondary" onClick={onRetry}>
          <Icon name="retry" size={15} strokeWidth={2.2} />
          Retry
        </button>
      )}
    </div>
  );
}
