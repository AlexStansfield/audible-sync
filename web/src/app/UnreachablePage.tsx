/**
 * The service cannot be reached at all (Unreachable.dc.html): a full page without the
 * sidebar, a countdown to the next automatic check, Retry now, and the commands to
 * look at the container. The app returns to where you were when it answers.
 */
import { useEffect } from "react";
import styles from "../auth/auth.module.css";
import { Icon } from "../components/Icon";
import own from "./UnreachablePage.module.css";

export function UnreachablePage({
  secondsLeft,
  checking,
  onRetry,
}: {
  secondsLeft: number;
  checking: boolean;
  onRetry: () => void;
}) {
  useEffect(() => {
    document.title = "Cannot reach Audible Sync";
  }, []);

  return (
    <div className={styles.centred}>
      <main className={own.box}>
        <span className={own.icon}>
          <Icon name="offline" size={30} />
        </span>
        <h1 className={own.title}>Cannot reach Audible Sync</h1>
        <p className={own.text}>
          The service is not answering. It may be restarting, or the container has stopped. Nothing is lost: runs pick
          up where they left off when it is back.
        </p>
        <div role="status" className={own.countdown}>
          <Icon name="spinner" size={16} strokeWidth={2.4} className={own.spinner} />
          {checking ? "Trying again now…" : `Trying again in ${secondsLeft} second${secondsLeft === 1 ? "" : "s"}`}
        </div>
        <button type="button" className="btn btn-primary btn-tall" onClick={onRetry} disabled={checking}>
          Retry now
        </button>
        <div className={own.help}>
          <span className={own.helpTitle}>Running it yourself? Check the container:</span>
          <code className={own.command}>docker compose ps audible-sync</code>
          <code className={own.command}>docker compose logs --tail 50 audible-sync</code>
        </div>
      </main>
    </div>
  );
}
