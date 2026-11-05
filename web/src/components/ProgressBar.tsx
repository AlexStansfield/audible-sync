import styles from "./ProgressBar.module.css";

/**
 * A progress bar: `value` of `max`, or indeterminate (no `aria-valuenow`, a sliding
 * stripe) when the size is unknown.
 */
export function ProgressBar({ value, max = 100, label }: { value?: number | null; max?: number; label: string }) {
  const known = value != null && max > 0;
  const percent = known ? Math.min(100, Math.max(0, (value / max) * 100)) : 0;
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={known ? 0 : undefined}
      aria-valuemax={known ? 100 : undefined}
      aria-valuenow={known ? Math.round(percent) : undefined}
      className={styles.track}
    >
      <span
        className={known ? styles.fill : styles.indeterminate}
        style={known ? { width: `${percent}%` } : undefined}
      />
    </div>
  );
}
