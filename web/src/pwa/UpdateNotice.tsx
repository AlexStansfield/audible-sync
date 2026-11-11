/**
 * Update available: a new build of the web app is waiting (`updates.ts`). It stays among
 * the notifications until it is taken up or dismissed, and Reload moves this page to
 * the new build.
 */
import { useState } from "react";
import { Icon } from "../components/Icon";
import styles from "../components/Toasts.module.css";
import { useUpdate } from "./updates";

export function UpdateNotice() {
  const update = useUpdate();
  const [dismissed, setDismissed] = useState(false);
  if (!update.available || dismissed) return null;

  return (
    <div role="status" className={styles.toast}>
      <span className={`${styles.mark} ${styles.markInfo}`}>
        <Icon name="retry" size={15} strokeWidth={2.4} />
      </span>
      <span className={styles.text}>
        <span className={styles.title}>Update available</span>
        <span className={styles.detail}>A new version of Audible Sync is ready.</span>
      </span>
      <button type="button" className="btn btn-primary btn-small" onClick={update.apply}>
        Reload
      </button>
      <button type="button" className={styles.dismiss} aria-label="Dismiss" onClick={() => setDismissed(true)}>
        <Icon name="close" size={14} strokeWidth={2.4} />
      </button>
    </div>
  );
}
