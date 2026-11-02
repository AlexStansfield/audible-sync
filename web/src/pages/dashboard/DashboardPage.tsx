/**
 * The Dashboard (Main.dc.html): the run in flight, the library's totals, each account,
 * what downloaded lately, what runs next, what needs attention and the recent runs.
 * A new install with no accounts gets the welcome card instead (`firstRun`).
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type CSSProperties, useEffect, useRef } from "react";
import { Link } from "react-router";
import { api, unwrap } from "../../api/client";
import { type Status, useMe, useStatus } from "../../api/queries";
import type { components } from "../../api/schema";
import { useSyncActions } from "../../api/useSyncActions";
import { OutcomeChip } from "../../components/Chip";
import { Cover } from "../../components/Cover";
import { ErrorCard } from "../../components/ErrorCard";
import { Icon } from "../../components/Icon";
import { ProgressBar } from "../../components/ProgressBar";
import { Skeleton, SkeletonCard } from "../../components/Skeleton";
import { useToast } from "../../components/Toasts";
import { formatBytes, formatClock, formatDay, formatRelative, formatTime, plural } from "../../lib/format";
import styles from "./Dashboard.module.css";
import { useBook, useLatestFailure, useRecentlyDownloaded, useRecentRuns, useStats, useUpNext } from "./queries";
import { bookOfRun, requeuedText, runDetail, stageLabel, subtitle, upNextHeader } from "./text";

type Account = components["schemas"]["AccountOut"];
type Stats = components["schemas"]["Stats"];
type RunSnapshot = components["schemas"]["RunSnapshot"];

export function DashboardPage() {
  const status = useStatus();
  const me = useMe();
  const accounts = status.data?.accounts ?? [];
  const firstRun = status.data !== undefined && accounts.length === 0;

  useRefreshDuringRun(status.data);

  return (
    <>
      <Header status={status.data} firstRun={firstRun} />
      {me.data?.generated_password && <PasswordNudge />}
      {firstRun && <Welcome />}
      {status.data?.current_run && <RunCard run={status.data.current_run} />}
      <Totals dimmed={firstRun} />
      {status.data && !firstRun && (
        <div className={styles.columns}>
          <div className={styles.wide}>
            <AccountsCard />
            <RecentCard />
          </div>
          <div className={styles.narrow}>
            <UpNextCard status={status.data} />
            <AttentionCard accounts={accounts} />
            <RunsCard accounts={accounts} />
          </div>
        </div>
      )}
    </>
  );
}

/**
 * While a run downloads, what it changes shows as it goes: each book it finishes
 * refetches the totals, the recent downloads and the queue (the end of the run
 * refetches everything anyway).
 */
function useRefreshDuringRun(status: Status | undefined) {
  const client = useQueryClient();
  const done = status?.current_run?.books_done;
  const seen = useRef(done);
  useEffect(() => {
    if (done !== undefined && seen.current !== undefined && done !== seen.current) {
      for (const queryKey of [["stats"], ["books", "recent"], ["queue"]]) {
        void client.invalidateQueries({ queryKey });
      }
    }
    seen.current = done;
  }, [done, client]);
}

function Header({ status, firstRun }: { status: Status | undefined; firstRun: boolean }) {
  const { start, cancel } = useSyncActions();
  const scheduler = status?.scheduler;
  const running = scheduler?.running ?? false;
  return (
    <header className="page-header">
      <div className={styles.titleBlock}>
        <h1 className="page-title">Dashboard</h1>
        <span className="page-subtitle">
          {scheduler ? subtitle(status.accounts.length, scheduler) : <Skeleton width={220} />}
        </span>
      </div>
      {scheduler && !firstRun && (
        <div className={styles.headerActions}>
          {scheduler.next_run_at && (
            <span className={styles.nextRun}>
              Next run <strong>{formatClock(scheduler.next_run_at)}</strong> ({formatRelative(scheduler.next_run_at)})
            </span>
          )}
          {running ? (
            <button
              type="button"
              className="btn btn-secondary"
              disabled={cancel.isPending || status?.current_run?.stage === "cancelling"}
              onClick={() => cancel.mutate()}
            >
              {status?.current_run?.stage === "cancelling" ? "Cancelling…" : "Cancel run"}
            </button>
          ) : (
            <button type="button" className="btn btn-primary" disabled={start.isPending} onClick={() => start.mutate()}>
              <Icon name="retry" size={16} strokeWidth={2.4} />
              Sync now
            </button>
          )}
        </div>
      )}
    </header>
  );
}

function PasswordNudge() {
  return (
    <div role="status" className={`banner-warn ${styles.nudge}`}>
      <Icon name="lock" size={20} />
      <span className={styles.nudgeText}>
        <strong>You are using the generated password.</strong>{" "}
        <span className={styles.secondary}>Choose your own so the one in the service log stops working.</span>
      </span>
      <Link to="/profile" className="btn btn-secondary btn-small">
        Change password
      </Link>
    </div>
  );
}

const WELCOME_STEPS = [
  {
    title: "Add an Audible account",
    text: "Sign in with a code at amazon.co.uk/code (or your marketplace's). Your password never passes through this app.",
  },
  { title: "Choose what to download", text: "Queue the whole library, or add it unmonitored and pick books yourself." },
  { title: "Let it run", text: "The first sync starts straight away, then on the schedule." },
];

function Welcome() {
  return (
    <section aria-labelledby="welcome-h" className={`card ${styles.welcome}`}>
      <div className={styles.welcomeText}>
        <h2 id="welcome-h" className={styles.welcomeTitle}>
          Welcome to Audible Sync
        </h2>
        <p className={styles.welcomeLead}>
          Connect an Audible account and the service fetches your library, downloads the books you own and files them as
          DRM-free audiobooks. Nothing has run yet.
        </p>
        <Link to="/accounts" className={`btn btn-primary btn-tall ${styles.start}`}>
          <Icon name="plus" size={16} strokeWidth={2.6} />
          Add an Audible account
        </Link>
      </div>
      <ol className={styles.steps}>
        {WELCOME_STEPS.map((step, index) => (
          <li key={step.title}>
            <span className={styles.stepNumber} aria-hidden="true">
              {index + 1}
            </span>
            <span className={styles.stepText}>
              <span className={styles.strong}>{step.title}</span>
              <span className={styles.muted}>{step.text}</span>
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}

function RunCard({ run }: { run: RunSnapshot }) {
  // The library row can be gone (id null) while the run still names the book
  const bookId = run.book?.id ?? undefined;
  const book = useBook(bookId);
  const transfer = run.transfer;
  const account = run.account as { name?: string } | null;
  const meta = [`Run #${run.run_id}`, account?.name, `started ${formatClock(run.started_at)}`].filter(Boolean);
  const ofRun = bookOfRun(run);

  return (
    <section aria-label="Run in progress" className={`card ${styles.run}`}>
      {bookId !== undefined ? (
        <Cover bookId={bookId} size={84} className={styles.runCover} />
      ) : (
        <span className={styles.runIcon}>
          <Icon name="retry" size={30} />
        </span>
      )}
      <div className={styles.runBody}>
        <div className={styles.runMeta}>
          <span className="chip" style={{ "--chip": "var(--accent-text)" } as CSSProperties}>
            <span className={styles.pulse} />
            {stageLabel(run)}
          </span>
          <span className={styles.muted}>{meta.join(" · ")}</span>
        </div>
        <div className={styles.runTitle}>
          {run.book ? (
            <>
              {bookId !== undefined ? (
                <Link to={`/library/${bookId}`} className={styles.plainLink}>
                  {run.book.title}
                </Link>
              ) : (
                run.book.title
              )}
              {book.data?.authors[0] && <span className={styles.by}> by {book.data.authors.join(", ")}</span>}
            </>
          ) : run.stage === "cancelling" ? (
            "Stopping after the current step"
          ) : (
            "Reading the library from Audible"
          )}
        </div>
        <div className={styles.progress}>
          <ProgressBar
            label="Current download"
            value={transfer?.total ? transfer.bytes : null}
            max={transfer?.total ?? undefined}
          />
          <div className={styles.progressText}>
            {transfer && (
              <span>
                {transfer.total
                  ? `${formatBytes(transfer.bytes)} of ${formatBytes(transfer.total)}`
                  : `${formatBytes(transfer.bytes)} so far · size unknown`}
              </span>
            )}
            {ofRun && <span>{ofRun}</span>}
          </div>
        </div>
      </div>
    </section>
  );
}

const TILES = [
  { key: "total", label: "In library", colour: "var(--text)", href: "/library" },
  { key: "downloaded", label: "Downloaded", colour: "var(--ok)", href: "/library?status=downloaded" },
  { key: "waiting_download", label: "Waiting", colour: "var(--info)", href: "/library?status=waiting_download" },
  { key: "unavailable", label: "Unavailable", colour: "var(--violet)", href: "/library?status=unavailable" },
  { key: "failed", label: "Failed", colour: "var(--bad)", href: "/library?status=failed" },
] as const;

function tileText(stats: Stats | undefined, key: (typeof TILES)[number]["key"]): { value: number; sub: string } {
  const books = stats?.books;
  const count = (status: string) => books?.by_status[status] ?? 0;
  switch (key) {
    case "total":
      return {
        value: books?.total ?? 0,
        sub: books?.total ? `${books.monitored.toLocaleString()} monitored` : "No books yet",
      };
    case "downloaded":
      return { value: count("downloaded"), sub: `${formatBytes(stats?.bytes_on_disk ?? 0)} on disk` };
    case "waiting_download":
      return { value: count("waiting_download"), sub: "Queued for the next run" };
    case "unavailable":
      return { value: count("unavailable"), sub: "Parked by Audible" };
    case "failed":
      return { value: count("failed"), sub: "Out of attempts" };
  }
}

function Totals({ dimmed }: { dimmed: boolean }) {
  const stats = useStats();
  if (stats.isError) {
    return (
      <ErrorCard title="Could not load the library totals" error={stats.error} onRetry={() => void stats.refetch()} />
    );
  }
  return (
    <section
      aria-label="Library summary"
      aria-busy={stats.isPending}
      className={`${styles.tiles} ${dimmed ? styles.dimmed : ""}`}
    >
      {TILES.map((tile) => {
        const { value, sub } = tileText(stats.data, tile.key);
        return (
          <Link key={tile.key} to={tile.href} className={styles.tile}>
            <span className={styles.tileLabel}>
              <span className={styles.tileSwatch} style={{ background: tile.colour }} />
              {tile.label}
            </span>
            <span className={styles.tileValue}>
              {stats.isPending ? <Skeleton width={56} height={28} /> : value.toLocaleString()}
            </span>
            <span className={styles.tileSub}>{stats.isPending ? <Skeleton width={90} height={10} /> : sub}</span>
          </Link>
        );
      })}
    </section>
  );
}

function CardHeader({ id, title, link, linkText }: { id: string; title: string; link?: string; linkText?: string }) {
  return (
    <div className={styles.cardHead}>
      <h2 id={id} className="card-title">
        {title}
      </h2>
      {link && (
        <Link to={link} className={styles.headLink}>
          {linkText}
        </Link>
      )}
    </div>
  );
}

function AccountsCard() {
  const stats = useStats();
  if (stats.isPending) return <SkeletonCard rows={2} label="Loading the accounts" />;
  if (stats.isError) {
    return <ErrorCard title="Could not load the accounts" error={stats.error} onRetry={() => void stats.refetch()} />;
  }
  return (
    <section aria-labelledby="acc-h" className={`card ${styles.section}`}>
      <CardHeader id="acc-h" title="Accounts" link="/accounts" linkText="Manage" />
      {stats.data.accounts.map(({ account, books, bytes_on_disk, last_run }) => {
        const count = (status: string) => books.by_status[status] ?? 0;
        const parts = [
          { status: "downloaded", label: "downloaded", colour: "var(--ok)" },
          { status: "waiting_download", label: "waiting", colour: "var(--info)" },
          { status: "unavailable", label: "unavailable", colour: "var(--violet)" },
          { status: "failed", label: "failed", colour: "var(--bad)" },
        ];
        return (
          <div key={account.id} className={styles.accountRow}>
            <div className={styles.accountHead}>
              <span className={styles.cc}>{account.country_code.toUpperCase()}</span>
              <span className={styles.strong}>{account.name}</span>
              <span className={styles.muted}>
                {plural(books.total, "book")} · {formatBytes(bytes_on_disk)}
              </span>
              {account.needs_login && <span className={styles.badText}>Needs a sign-in</span>}
              {!account.enabled && <span className={styles.muted}>Sync off</span>}
              <span className={styles.lastRun}>
                {last_run ? `Last run ${formatTime(last_run.started_at)}` : "Not run yet"}
              </span>
            </div>
            <div className={styles.stack} aria-hidden="true">
              {parts.map((part) =>
                count(part.status) ? (
                  <span key={part.status} style={{ flex: count(part.status), background: part.colour }} />
                ) : null,
              )}
            </div>
            <div className={styles.legend}>
              {parts.map((part) => (
                <span key={part.status}>
                  {count(part.status).toLocaleString()} {part.label}
                </span>
              ))}
            </div>
          </div>
        );
      })}
    </section>
  );
}

function RecentCard() {
  const recent = useRecentlyDownloaded();
  if (recent.isPending) return <SkeletonCard rows={2} label="Loading the recent downloads" />;
  if (recent.isError) {
    return (
      <ErrorCard
        title="Could not load the recent downloads"
        error={recent.error}
        onRetry={() => void recent.refetch()}
      />
    );
  }
  return (
    <section aria-labelledby="recent-h" className={`card ${styles.section}`}>
      <CardHeader id="recent-h" title="Recently downloaded" link="/library?status=downloaded" linkText="View library" />
      {recent.data.items.length === 0 ? (
        <p className={styles.empty}>Nothing downloaded yet. Books appear here as runs file them.</p>
      ) : (
        <div className={styles.covers}>
          {recent.data.items.map((book) => (
            <Link key={book.id} to={`/library/${book.id}`} className={styles.coverLink}>
              <Cover bookId={book.id} className={styles.coverImage} />
              <span className={styles.coverTitle}>{book.title}</span>
              <span className={styles.coverAuthor}>{book.authors.join(", ")}</span>
            </Link>
          ))}
        </div>
      )}
    </section>
  );
}

function UpNextCard({ status }: { status: Status }) {
  const queue = useUpNext();
  if (queue.isPending) return <SkeletonCard rows={4} label="Loading what runs next" />;
  if (queue.isError) {
    return <ErrorCard title="Could not load what runs next" error={queue.error} onRetry={() => void queue.refetch()} />;
  }
  const { items, waiting, max_download } = queue.data;
  const more = waiting - items.length;
  const codes = new Map(status.accounts.map((account) => [account.id, account.country_code.toUpperCase()]));
  return (
    <section aria-labelledby="next-h" className={`card ${styles.section} ${styles.tight}`}>
      <div className={styles.upNextHead}>
        <h2 id="next-h" className="card-title">
          Up next
        </h2>
        <span className={styles.small}>{upNextHeader(status.scheduler, max_download)}</span>
      </div>
      {items.length === 0 ? (
        <p className={styles.empty}>Nothing waiting. New purchases are queued as the sync finds them.</p>
      ) : (
        <ol className={styles.list}>
          {items.map(({ position, account_id, book }) => (
            <li key={book.id}>
              <Link to={`/library/${book.id}`} className={styles.row}>
                <span className={styles.position}>{position}</span>
                <Cover bookId={book.id} size={36} />
                <span className={styles.rowText}>
                  <span className={styles.rowTitle}>{book.title}</span>
                  <span className={styles.small}>
                    {[book.authors[0], book.date_added && `bought ${formatDay(book.date_added)}`]
                      .filter(Boolean)
                      .join(" · ")}
                  </span>
                </span>
                {codes.size > 1 && <span className={styles.cc}>{codes.get(account_id)}</span>}
              </Link>
            </li>
          ))}
        </ol>
      )}
      {more > 0 && (
        <Link to="/library?status=waiting_download&monitored=true" className={styles.more}>
          +{more.toLocaleString()} more waiting
        </Link>
      )}
    </section>
  );
}

function AttentionCard({ accounts }: { accounts: Account[] }) {
  const stats = useStats();
  const toast = useToast();
  const client = useQueryClient();
  const unavailable = stats.data?.books.by_status.unavailable ?? 0;
  const failed = stats.data?.books.by_status.failed ?? 0;
  const latest = useLatestFailure(failed > 0);
  const signIn = accounts.filter((account) => account.needs_login);
  const retry = useMutation({
    mutationFn: () => unwrap(api.POST("/api/books/retry-unavailable", { body: {} })),
    onSuccess: ({ requeued }) => {
      toast.success(requeuedText(requeued));
      for (const queryKey of [["stats"], ["books"], ["queue"]]) void client.invalidateQueries({ queryKey });
    },
    onError: (error) => toast.error("Could not retry the unavailable books", error),
  });

  if (!unavailable && !failed && !signIn.length) return null;
  return (
    <section aria-labelledby="att-h" className={`card ${styles.section}`}>
      <h2 id="att-h" className="card-title">
        Needs attention
      </h2>
      {signIn.map((account) => (
        <div key={account.id} className={styles.attention}>
          <Icon name="accounts" className={styles.badIcon} />
          <div className={styles.attentionBody}>
            <span className={styles.strong}>{account.name} needs a sign-in</span>
            <span className={styles.muted}>Its Audible registration stopped working, so it is skipped until then.</span>
            <Link to="/accounts" className={styles.actionLink}>
              Sign in again
            </Link>
          </div>
        </div>
      ))}
      {unavailable > 0 && (
        <div className={styles.attention}>
          <Icon name="clock" className={styles.violetIcon} />
          <div className={styles.attentionBody}>
            <span className={styles.strong}>{plural(unavailable, "book")} unavailable</span>
            <span className={styles.muted}>
              Audible refused the licence or withdrew the Plus title. Licence refusals can be retried.
            </span>
            <button
              type="button"
              className="btn btn-secondary btn-small"
              disabled={retry.isPending}
              onClick={() => retry.mutate()}
            >
              Retry unavailable
            </button>
          </div>
        </div>
      )}
      {failed > 0 && (
        <div className={styles.attention}>
          <Icon name="warning" className={styles.badIcon} />
          <div className={styles.attentionBody}>
            <span className={styles.strong}>{plural(failed, "book")} failed</span>
            <span className={styles.muted}>
              Out of attempts.
              {latest.data?.last_error && ` Latest: ${latest.data.title}: ${latest.data.last_error}`}
            </span>
            <Link to="/library?status=failed" className={styles.actionLink}>
              Review failed books
            </Link>
          </div>
        </div>
      )}
    </section>
  );
}

function RunsCard({ accounts }: { accounts: Account[] }) {
  const runs = useRecentRuns();
  if (runs.isPending) return <SkeletonCard rows={5} label="Loading the recent runs" />;
  if (runs.isError) {
    return <ErrorCard title="Could not load the recent runs" error={runs.error} onRetry={() => void runs.refetch()} />;
  }
  const names = new Map(accounts.map((account) => [account.id, account.name]));
  return (
    <section aria-labelledby="runs-h" className={`card ${styles.section} ${styles.tight}`}>
      <CardHeader id="runs-h" title="Recent runs" link="/activity" linkText="All activity" />
      {runs.data.items.length === 0 ? (
        <p className={styles.empty}>No runs yet.</p>
      ) : (
        <ul className={styles.list}>
          {runs.data.items.map((run) => (
            <li key={run.id}>
              <Link to="/activity" className={styles.row}>
                <span className={styles.outcome}>
                  <OutcomeChip outcome={run.outcome} />
                </span>
                <span className={styles.rowText}>
                  <span className={styles.runWhen}>
                    {[formatTime(run.started_at), run.account_id != null && names.get(run.account_id)]
                      .filter(Boolean)
                      .join(" · ")}
                  </span>
                  <span className={styles.small}>{runDetail(run)}</span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
