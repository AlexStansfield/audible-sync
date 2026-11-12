/**
 * The Wishlist (Wishlist.dc.html): every account's wishlist in one list, as last read
 * from Audible, with Remove (confirmed), Refresh from Audible, and a book a library
 * already holds shown prominently, since that is the reason to remove it.
 */
import { type ReactNode, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { useStatus } from "../../api/queries";
import type { components } from "../../api/schema";
import { BookPanel } from "../../catalog/BookPanel";
import { BookRow, type CatalogContext } from "../../catalog/BookRow";
import {
  useWishlist,
  useWishlistChange,
  useWishlistCount,
  useWishlistRefresh,
  WISHLIST_PAGE_SIZE,
  type WishlistSort,
} from "../../catalog/queries";
import { wishlistName } from "../../catalog/text";
import { Dialog } from "../../components/Dialog";
import { ErrorCard } from "../../components/ErrorCard";
import { Icon } from "../../components/Icon";
import { Pager } from "../../components/Pager";
import { Skeleton } from "../../components/Skeleton";
import { useToast } from "../../components/Toasts";
import { formatDay, formatRelative, plural } from "../../lib/format";
import { positiveId } from "../../lib/ids";
import styles from "./Wishlist.module.css";

type Item = components["schemas"]["WishlistItemOut"];
type AccountState = components["schemas"]["WishlistAccountOut"];
type Account = components["schemas"]["AccountOut"];

type WishlistQuery = { accountId: number | null; sort: WishlistSort; page: number; book: string | null };

const SORTS: readonly { value: WishlistSort; label: string }[] = [
  { value: "date_added", label: "Date added, newest first" },
  { value: "title", label: "Title" },
  { value: "release_date", label: "Release date" },
];

export function parseWishlistQuery(params: URLSearchParams): WishlistQuery {
  const book = params.get("book");
  return {
    accountId: positiveId(params.get("account")),
    sort: SORTS.find((option) => option.value === params.get("sort"))?.value ?? "date_added",
    page: positiveId(params.get("page")) ?? 1,
    book: book && /^[A-Za-z0-9]{10}$/.test(book) ? book : null,
  };
}

function serialise(query: WishlistQuery): URLSearchParams {
  const params = new URLSearchParams();
  if (query.accountId !== null) params.set("account", String(query.accountId));
  if (query.sort !== "date_added") params.set("sort", query.sort);
  if (query.page !== 1) params.set("page", String(query.page));
  if (query.book) params.set("book", query.book);
  return params;
}

export function WishlistPage() {
  const [params, setParams] = useSearchParams();
  const query = parseWishlistQuery(params);
  const status = useStatus();
  const list = useWishlist(query);
  const count = useWishlistCount();
  const refresh = useWishlistRefresh();
  const toast = useToast();
  // Taken on each render: the list refetches often enough for "2 hours ago"
  const now = new Date();
  const [removing, setRemoving] = useState<Item | null>(null);

  const accounts = status.data?.accounts ?? [];
  const several = accounts.length > 1;
  const signedIn = accounts.filter((account) => !account.needs_login);
  const named = (id: number) => accounts.find((account) => account.id === id);
  const total = count.data ?? list.data?.total;
  const states = list.data?.accounts ?? [];
  const items = list.data?.items ?? [];

  const update = (changes: Partial<WishlistQuery>) =>
    setParams(serialise({ ...query, page: 1, book: null, ...changes }));

  const context: CatalogContext = {
    accounts,
    onOpen: (asin) => setParams(serialise({ ...query, book: asin })),
  };

  if (status.data !== undefined && signedIn.length === 0 && (total ?? 0) === 0) {
    return (
      <>
        <Header total={undefined} accounts={accounts.length} />
        <NoAccount />
      </>
    );
  }

  const pages = list.data ? Math.max(1, Math.ceil(list.data.total / WISHLIST_PAGE_SIZE)) : 1;
  const filtered = query.accountId !== null;
  const held = items.filter((item) => item.in_library).length;
  const opened = items.find((item) => item.asin === query.book);
  const openedAccount = opened ? named(opened.account_id) : undefined;

  return (
    <>
      <Header
        total={total}
        accounts={accounts.length}
        right={
          <div className={styles.headerActions}>
            <UpdatedLine states={states} now={now} refreshing={refresh.isPending} />
            <button
              type="button"
              className="btn btn-secondary"
              disabled={refresh.isPending || signedIn.length === 0}
              aria-busy={refresh.isPending || undefined}
              onClick={() =>
                refresh.mutate(undefined, {
                  onError: (error) => toast.error("Could not read the wishlists from Audible", error),
                })
              }
            >
              <Icon
                name={refresh.isPending ? "spinner" : "retry"}
                size={16}
                className={refresh.isPending ? styles.spin : undefined}
              />
              {refresh.isPending ? "Refreshing…" : "Refresh from Audible"}
            </button>
            <Link to="/search" className="btn btn-primary">
              <Icon name="search" size={16} />
              Find books
            </Link>
          </div>
        }
      />

      <Notices
        states={states}
        accounts={accounts}
        now={now}
        onRetry={() => refresh.mutate()}
        busy={refresh.isPending}
      />

      {list.isError && !list.data ? (
        <ErrorCard title="Could not load the wishlist" error={list.error} onRetry={() => void list.refetch()} />
      ) : !list.data ? (
        <ListSkeleton />
      ) : list.data.total === 0 && !filtered ? (
        <Empty several={several} />
      ) : (
        <>
          <section aria-label="Filters" className={styles.toolbar}>
            {several && (
              <label className={styles.selectLabel}>
                Account
                <select
                  className={styles.select}
                  value={query.accountId ?? ""}
                  onChange={(event) => update({ accountId: event.target.value ? Number(event.target.value) : null })}
                >
                  <option value="">All accounts</option>
                  {accounts.map((account) => (
                    <option key={account.id} value={account.id}>
                      {account.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <label className={styles.selectLabel}>
              Sort
              <select
                className={styles.select}
                value={query.sort}
                onChange={(event) => update({ sort: event.target.value as WishlistSort })}
              >
                {SORTS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            {held > 0 && (
              <span className={styles.heldCount}>
                <span className={styles.okDot} />
                {held} already in a library
              </span>
            )}
          </section>

          {list.data.total === 0 ? (
            <FilterEmpty
              account={query.accountId === null ? undefined : named(query.accountId)}
              others={total ?? 0}
              onShowAll={() => update({ accountId: null })}
            />
          ) : (
            <>
              <div className={styles.card}>
                {items.map((item) => {
                  const account = named(item.account_id);
                  return (
                    <div
                      key={`${item.account_id}-${item.asin}`}
                      className={removing === item ? styles.dimmed : undefined}
                    >
                      <BookRow
                        book={item}
                        context={{ ...context, countryCode: account?.country_code }}
                        held={item.in_library}
                        above={<RowAbove item={item} account={account} several={several} now={now} />}
                        action={
                          <button type="button" className="btn btn-secondary" onClick={() => setRemoving(item)}>
                            Remove
                          </button>
                        }
                      />
                    </div>
                  );
                })}
              </div>
              <Pager
                page={Math.min(query.page, pages)}
                pages={pages}
                label={plural(list.data.total, "book")}
                onPage={(page) => setParams(serialise({ ...query, page }))}
              />
            </>
          )}
        </>
      )}

      <RemoveDialog item={removing} accounts={accounts} onDone={() => setRemoving(null)} />

      {query.book && (
        <BookPanel
          asin={query.book}
          shown={opened}
          accountId={opened?.account_id ?? null}
          context={{ ...context, countryCode: openedAccount?.country_code }}
          onClose={() => setParams(serialise({ ...query, book: null }))}
        />
      )}
    </>
  );
}

function Header({ total, accounts, right }: { total: number | undefined; accounts: number; right?: ReactNode }) {
  return (
    <header className="page-header">
      <div className={styles.titleBlock}>
        <h1 className="page-title">Wishlist</h1>
        <span className="page-subtitle">
          {total === undefined ? (
            <Skeleton width={200} />
          ) : accounts > 1 ? (
            `${plural(total, "book")} on the wishlists of ${accounts} accounts`
          ) : (
            `${plural(total, "book")} on your wishlist`
          )}
        </span>
      </div>
      {right}
    </header>
  );
}

/** "Updated 2 hours ago", from the newest read that worked. */
function UpdatedLine({ states, now, refreshing }: { states: AccountState[]; now: Date; refreshing: boolean }) {
  if (refreshing) return <span className={styles.updated}>Reading the wishlists from Audible…</span>;
  const reads = states.map((state) => state.read_at).filter((read): read is string => Boolean(read));
  if (reads.length === 0) return null;
  const newest = reads.sort().at(-1) as string;
  return <span className={styles.updated}>Updated {formatRelative(newest, now)}</span>;
}

/** A wishlist that could not be read, or an account that needs signing in. */
function Notices({
  states,
  accounts,
  now,
  onRetry,
  busy,
}: {
  states: AccountState[];
  accounts: Account[];
  now: Date;
  onRetry: () => void;
  busy: boolean;
}) {
  const named = (id: number) => accounts.find((account) => account.id === id)?.name ?? `Account ${id}`;
  const failed = states.filter((state) => state.error && !state.needs_login);
  const signIn = states.filter((state) => state.needs_login);
  if (!failed.length && !signIn.length) return null;
  return (
    <div className={styles.notices}>
      {failed.map((state) => (
        <div key={state.account_id} role="status" className="banner-warn">
          <Icon name="warning" size={18} />
          <span className={styles.noticeText}>
            <strong>{named(state.account_id)}'s wishlist could not be read.</strong> Audible did not answer. The other
            accounts' wishlists are shown;{" "}
            {state.read_at
              ? `${named(state.account_id)}'s books are as of the last read, ${formatRelative(state.read_at, now)}.`
              : `${named(state.account_id)}'s has not been read yet.`}
          </span>
          <button type="button" className="btn btn-secondary btn-small" disabled={busy} onClick={onRetry}>
            Try again
          </button>
        </div>
      ))}
      {signIn.map((state) => (
        <div key={state.account_id} role="status" className="banner-warn">
          <Icon name="warning" size={18} />
          <span className={styles.noticeText}>
            <strong>{named(state.account_id)} needs signing in again</strong> before its wishlist can be read.
          </span>
          <Link to="/accounts" className="btn btn-secondary btn-small">
            Go to Accounts
          </Link>
        </div>
      ))}
    </div>
  );
}

function RowAbove({
  item,
  account,
  several,
  now,
}: {
  item: Item;
  account: Account | undefined;
  several: boolean;
  now: Date;
}) {
  return (
    <p className={styles.above}>
      {several && account && (
        <>
          <span className={styles.code}>{account.country_code.toUpperCase()}</span>
          <span className={styles.accountName}>{account.name}</span>
          <span aria-hidden="true">·</span>
        </>
      )}
      {item.added_at && <span>Added {addedText(item.added_at, now)}</span>}
    </p>
  );
}

function addedText(iso: string, now: Date): string {
  const date = new Date(iso);
  if (now.getTime() - date.getTime() < 24 * 60 * 60_000 && date.getDate() === now.getDate()) return "today";
  return formatDay(iso, now);
}

function RemoveDialog({ item, accounts, onDone }: { item: Item | null; accounts: Account[]; onDone: () => void }) {
  const { remove } = useWishlistChange();
  const toast = useToast();
  const account = item ? accounts.find((one) => one.id === item.account_id) : undefined;
  const where = wishlistName(account, accounts.length);
  const confirm = () => {
    if (!item) return;
    remove.mutate(
      { accountId: item.account_id, asin: item.asin },
      {
        onSuccess: () => {
          toast.success(`Removed ${item.title} from ${where}`);
          onDone();
        },
        onError: (error) => {
          toast.error(`Could not remove ${item.title}`, error);
          onDone();
        },
      },
    );
  };
  return (
    <Dialog
      open={item !== null}
      onClose={remove.isPending ? () => undefined : onDone}
      title={item ? `Remove ${item.title} from ${where}?` : ""}
      actions={
        <>
          <button type="button" className="btn btn-secondary" disabled={remove.isPending} onClick={onDone}>
            Keep it
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={remove.isPending}
            aria-busy={remove.isPending || undefined}
            onClick={confirm}
          >
            {remove.isPending ? "Removing…" : "Remove from wishlist"}
          </button>
        </>
      }
    >
      <p className={styles.dialogText}>It comes off the wishlist on Audible too. You can add it back from Search.</p>
    </Dialog>
  );
}

function ListSkeleton() {
  return (
    <div className={styles.card} role="status" aria-busy="true" aria-label="Loading the wishlist">
      {Array.from({ length: 5 }, (_, index) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: identical placeholders
        <div key={index} className={styles.skeletonRow}>
          <Skeleton width={88} height={88} style={{ borderRadius: 6 }} />
          <span className={styles.skeletonLines}>
            <Skeleton width="30%" />
            <Skeleton width="55%" height={14} />
            <Skeleton width="40%" />
          </span>
        </div>
      ))}
    </div>
  );
}

function Empty({ several }: { several: boolean }) {
  return (
    <section aria-labelledby="empty-h" className={`${styles.emptyCard} ${styles.dashed}`}>
      <span className={styles.emptyIcon}>
        <Icon name="heart" size={26} />
      </span>
      <h2 id="empty-h" className={styles.emptyTitle}>
        {several ? "Every wishlist is empty" : "Your wishlist is empty"}
      </h2>
      <p className={styles.emptyText}>
        Search the catalogue to add books. Books added on Audible itself show up after the next sync, or straight away
        with Refresh from Audible.
      </p>
      <Link to="/search" className="btn btn-primary">
        Search the catalogue
      </Link>
    </section>
  );
}

function FilterEmpty({
  account,
  others,
  onShowAll,
}: {
  account: Account | undefined;
  others: number;
  onShowAll: () => void;
}) {
  return (
    <section aria-labelledby="fempty-h" className={styles.emptyCard}>
      <h2 id="fempty-h" className={styles.emptyTitle}>
        {account ? `${account.name}'s wishlist is empty` : "This wishlist is empty"}
      </h2>
      {others > 0 && <p className={styles.emptyText}>The other accounts have {plural(others, "book")} on theirs.</p>}
      <div className={styles.actions}>
        <button type="button" className="btn btn-secondary" onClick={onShowAll}>
          Show all accounts
        </button>
        <Link to="/search" className="btn btn-primary">
          Search the catalogue
        </Link>
      </div>
    </section>
  );
}

function NoAccount() {
  return (
    <section aria-labelledby="noacc-h" className={`${styles.emptyCard} ${styles.dashed}`}>
      <span className={styles.emptyIcon}>
        <Icon name="accounts" size={26} />
      </span>
      <h2 id="noacc-h" className={styles.emptyTitle}>
        No wishlist to show
      </h2>
      <p className={styles.emptyText}>Add an Audible account, or sign in to one, to see its wishlist here.</p>
      <Link to="/accounts" className="btn btn-primary">
        Go to Accounts
      </Link>
    </section>
  );
}
