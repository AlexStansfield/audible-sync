import type { CSSProperties } from "react";
import styles from "./Skeleton.module.css";

/** One grey block in the shape of what is loading. */
export function Skeleton({
  width,
  height = 12,
  style,
}: {
  width?: number | string;
  height?: number;
  style?: CSSProperties;
}) {
  return <span className={styles.block} style={{ width, height, ...style }} />;
}

/** A card of rows while its content loads (Patterns.dc.html, "Loading: a card"). */
export function SkeletonCard({ rows = 3, label = "Loading" }: { rows?: number; label?: string }) {
  const widths = ["70%", "55%", "62%", "48%", "66%"];
  return (
    <div className={`card ${styles.card}`} role="status" aria-busy="true" aria-label={label}>
      <div className={styles.head}>
        <Skeleton width={120} height={14} />
        <Skeleton width={56} height={12} style={{ marginLeft: "auto" }} />
      </div>
      {Array.from({ length: rows }, (_, i) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: identical placeholders
        <div key={i} className={styles.row}>
          <Skeleton width={36} height={36} style={{ borderRadius: 4 }} />
          <span className={styles.lines}>
            <Skeleton width={widths[i % widths.length]} />
            <Skeleton width="40%" height={10} />
          </span>
        </div>
      ))}
    </div>
  );
}
