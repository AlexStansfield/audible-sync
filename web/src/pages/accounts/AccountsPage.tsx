/**
 * Accounts (DESIGN.md, "Accounts" and "Accounts: dialogs"): one card per Audible
 * marketplace login with its state, a Sync switch, Sign in (again) and a menu with
 * Rename and Remove; the help cards; and the sign-in dialog for adding one.
 */
import { useMutationState } from "@tanstack/react-query";
import { type FormEvent, type KeyboardEvent, useEffect, useId, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import { Chip } from "../../components/Chip";
import { ConfirmDialog, Dialog } from "../../components/Dialog";
import { ErrorCard, errorDetail } from "../../components/ErrorCard";
import { Icon } from "../../components/Icon";
import { SkeletonCard } from "../../components/Skeleton";
import { Switch } from "../../components/Switch";
import { formatDate, formatTime } from "../../lib/format";
import { useStats } from "../dashboard/queries";
import styles from "./Accounts.module.css";
import { type Account, accountDetail, accountState, type Marketplace, type Purpose, removeText } from "./login";
import { useAccounts, useMarketplaces, useRemove, useRename, useSetEnabled } from "./queries";
import { SignInDialog } from "./SignInDialog";

type Open =
  | { kind: "sign-in"; purpose: Purpose }
  | { kind: "rename"; account: Account }
  | { kind: "remove"; account: Account }
  | null;

export function AccountsPage() {
  const accounts = useAccounts();
  const marketplaces = useMarketplaces();
  const stats = useStats();
  const [open, setOpen] = useState<Open>(null);
  const close = () => setOpen(null);
  const addAccount = () => setOpen({ kind: "sign-in", purpose: { kind: "add" } });

  const statsFor = (id: number) => stats.data?.accounts.find((entry) => entry.account.id === id);

  return (
    <div className={styles.page}>
      <header className="page-header">
        <div className={styles.heading}>
          <h1 className="page-title">Accounts</h1>
          <span className="page-subtitle">
            One account per Audible marketplace. Your Amazon password never passes through Audible Sync.
          </span>
        </div>
        <button type="button" className={`btn btn-primary ${styles.add}`} onClick={addAccount}>
          <Icon name="plus" size={16} strokeWidth={2.6} />
          Add account
        </button>
      </header>

      <section aria-label="Accounts" className={styles.list}>
        {accounts.isPending ? (
          <SkeletonCard rows={2} label="Loading the accounts" />
        ) : accounts.error ? (
          <ErrorCard title="Could not load the accounts" error={accounts.error} onRetry={() => accounts.refetch()} />
        ) : accounts.data.length === 0 ? (
          <div className={styles.empty}>
            <h2 className="card-title">No accounts yet</h2>
            <p className="muted">Add your Audible account to fetch your library. You sign in at Amazon, not here.</p>
            <button type="button" className="btn btn-primary" onClick={addAccount}>
              Add an Audible account
            </button>
          </div>
        ) : (
          accounts.data.map((account) => (
            <AccountCard
              key={account.id}
              account={account}
              marketplace={marketplaces.data?.find((entry) => entry.country_code === account.country_code)}
              books={statsFor(account.id)?.books.total}
              onSignIn={() => setOpen({ kind: "sign-in", purpose: { kind: "relogin", account } })}
              onRename={() => setOpen({ kind: "rename", account })}
              onRemove={() => setOpen({ kind: "remove", account })}
            />
          ))
        )}
      </section>

      <section aria-label="Help" className={styles.help}>
        <div className={styles.helpCard}>
          <h2>Downloads refused with "License not granted"?</h2>
          <p>
            Audible stopped licensing devices registered with the older app profile. Use Sign in again: the library and
            history are kept, the old device is removed from Amazon, and refused books are queued again.
          </p>
        </div>
        <div className={styles.helpCard}>
          <h2>Removing an account</h2>
          <p>
            Deletes its library rows and run history. Files already in your audiobooks folder stay. You can also remove
            the device from your Amazon account at the same time.
          </p>
        </div>
      </section>

      {open?.kind === "sign-in" && <SignInDialog purpose={open.purpose} onClose={close} />}
      {open?.kind === "rename" && <RenameDialog key={open.account.id} account={open.account} onClose={close} />}
      {open?.kind === "remove" && (
        <RemoveDialog
          key={open.account.id}
          account={open.account}
          downloaded={statsFor(open.account.id)?.books.by_status.downloaded}
          onClose={close}
        />
      )}
    </div>
  );
}

function AccountCard({
  account,
  marketplace,
  books,
  onSignIn,
  onRename,
  onRemove,
}: {
  account: Account;
  marketplace: Marketplace | undefined;
  books: number | undefined;
  onSignIn: () => void;
  onRename: () => void;
  onRemove: () => void;
}) {
  const setEnabled = useSetEnabled();
  // A second flip while the first is saving would roll back to the first's guess
  const pending = useMutationState({
    filters: { mutationKey: ["account-enabled"], status: "pending" },
    select: (mutation) => (mutation.state.variables as { id: number } | undefined)?.id,
  });
  const state = accountState(account);
  const nameId = `account-${account.id}-name`;

  return (
    <article className={`${styles.account} ${account.needs_login ? styles.accountBad : ""}`} aria-labelledby={nameId}>
      <span className={styles.market} aria-hidden="true">
        {account.country_code.toUpperCase() || "?"}
      </span>
      <div className={styles.who}>
        <div className={styles.nameRow}>
          <h2 id={nameId} className={styles.name}>
            {account.name}
          </h2>
          <Chip colour={state.colour}>{state.label}</Chip>
        </div>
        <span className={styles.detail}>{accountDetail(account, marketplace, (iso) => formatDate(iso))}</span>
      </div>
      <dl className={styles.figures}>
        <div>
          <dt>Books</dt>
          <dd>{books ?? "—"}</dd>
        </div>
        <div>
          <dt>Last synced</dt>
          <dd>{account.last_synced_at ? formatTime(account.last_synced_at) : "Never"}</dd>
        </div>
      </dl>
      <div className={styles.actions}>
        {account.needs_login ? (
          <button type="button" className="btn btn-primary" onClick={onSignIn}>
            Sign in
          </button>
        ) : (
          <button type="button" className="btn btn-secondary" onClick={onSignIn}>
            Sign in again
          </button>
        )}
        <span className={styles.sync}>
          <Switch
            checked={account.enabled}
            label={`Sync ${account.name}`}
            disabled={pending.includes(account.id)}
            onChange={(enabled) => setEnabled.mutate({ id: account.id, enabled })}
          />
          <span aria-hidden="true">Sync</span>
        </span>
        <AccountMenu name={account.name} onRename={onRename} onRemove={onRemove} />
      </div>
    </article>
  );
}

/**
 * The ⋯ menu: opens below its button with the first item focused; arrows move, Escape
 * or a click elsewhere closes it, and focus goes back to the button.
 */
function AccountMenu({ name, onRename, onRemove }: { name: string; onRename: () => void; onRemove: () => void }) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) return;
    wrap.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
    const away = (event: MouseEvent) => {
      if (!wrap.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);

  const keys = (event: KeyboardEvent) => {
    const items = [...(wrap.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [])];
    const at = items.indexOf(document.activeElement as HTMLElement);
    if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
      button.current?.focus();
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      items[(at + step + items.length) % items.length]?.focus();
    } else if (event.key === "Tab") {
      setOpen(false);
    }
  };

  const choose = (action: () => void) => () => {
    setOpen(false);
    action();
  };

  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: the keys belong to the menu inside
    <div ref={wrap} className={styles.menuWrap} onKeyDown={open ? keys : undefined}>
      <button
        ref={button}
        type="button"
        className={styles.more}
        aria-label={`More actions for ${name}`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => setOpen(!open)}
      >
        <Icon name="more" size={16} strokeWidth={0} filled />
      </button>
      {open && (
        <div id={menuId} role="menu" aria-label={`Actions for ${name}`} className={styles.menu}>
          <button type="button" role="menuitem" tabIndex={-1} onClick={choose(onRename)}>
            Rename
          </button>
          <button type="button" role="menuitem" tabIndex={-1} className={styles.menuBad} onClick={choose(onRemove)}>
            Remove account
          </button>
        </div>
      )}
    </div>
  );
}

function RenameDialog({ account, onClose }: { account: Account; onClose: () => void }) {
  const [name, setName] = useState(account.name);
  const rename = useRename();
  const trimmed = name.trim();

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!trimmed || trimmed === account.name) return onClose();
    rename.mutate({ id: account.id, name: trimmed }, { onSuccess: onClose });
  };

  return (
    <Dialog open onClose={rename.isPending ? () => undefined : onClose} title="Rename account" width={420}>
      <form className={styles.stack} onSubmit={submit}>
        <div className="field">
          <label htmlFor="rename">Name</label>
          <input
            id="rename"
            className="input"
            type="text"
            maxLength={100}
            value={name}
            onChange={(event) => setName(event.target.value)}
            aria-describedby="rename-hint"
          />
          <span id="rename-hint" className="hint">
            Only used in this app. Your Amazon account is not changed.
          </span>
        </div>
        {rename.error && (
          <p role="alert" className="alert-error">
            Could not rename the account: {errorDetail(rename.error)}
          </p>
        )}
        <div className={styles.dialogActions}>
          <button type="button" className="btn btn-secondary" onClick={onClose} disabled={rename.isPending}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={rename.isPending || !trimmed}>
            Save
          </button>
        </div>
      </form>
    </Dialog>
  );
}

function RemoveDialog({
  account,
  downloaded,
  onClose,
}: {
  account: Account;
  downloaded: number | undefined;
  onClose: () => void;
}) {
  const [deregister, setDeregister] = useState(true);
  const remove = useRemove();
  // 409: the run in flight is on this account
  const busy = remove.error instanceof ApiError && remove.error.status === 409;

  return (
    <ConfirmDialog
      open
      title={`Remove ${account.name}?`}
      confirmLabel="Remove account"
      cancelLabel="Keep account"
      busy={remove.isPending}
      onCancel={onClose}
      onConfirm={() =>
        remove.mutate({ id: account.id, deregister: deregister && !account.needs_login }, { onSuccess: onClose })
      }
    >
      <p>{removeText(downloaded)}</p>
      {!account.needs_login && (
        <label className={styles.checkCard}>
          <input type="checkbox" checked={deregister} onChange={(event) => setDeregister(event.target.checked)} />
          <span>
            <span className={styles.checkTitle}>Also remove this device from my Amazon account</span>
            <span className={styles.checkHint}>
              It disappears from Your Devices on Amazon. If Amazon cannot be reached, the account is removed anyway.
            </span>
          </span>
        </label>
      )}
      {remove.error && (
        <p role="alert" className="alert-error">
          {busy
            ? `A run is syncing ${account.name} right now. Cancel the run, or wait for it to move on, then remove it.`
            : `Could not remove the account: ${errorDetail(remove.error)}`}
        </p>
      )}
    </ConfirmDialog>
  );
}
