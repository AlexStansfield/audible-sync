/**
 * Add an account, or sign an existing one in again (DESIGN.md, "Add account dialog" and
 * "Accounts: dialogs"): setup, then a code to enter at Amazon - polled every 3 s until
 * it is confirmed, expires or fails - or the browser sign-in for older accounts, then
 * done. Mounted afresh for each opening, so nothing carries over.
 */
import { useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import { useSyncActions } from "../../api/useSyncActions";
import { Dialog } from "../../components/Dialog";
import { errorDetail } from "../../components/ErrorCard";
import { Icon } from "../../components/Icon";
import { useToast } from "../../components/Toasts";
import styles from "./Accounts.module.css";
import {
  type Account,
  type CodeState,
  defaultMarketplace,
  dialogTitle,
  doneText,
  type Login,
  type Marketplace,
  marketplaceLabel,
  type Purpose,
  remainingText,
  type Stage,
  shortAddress,
  stepIndex,
  stepLabels,
} from "./login";
import {
  isForgotten,
  type LoginChoice,
  refreshAfterAccountChange,
  useCompleteLogin,
  useLoginStatus,
  useMarketplaces,
  useStartLogin,
} from "./queries";

export function SignInDialog({ purpose, onClose }: { purpose: Purpose; onClose: () => void }) {
  const client = useQueryClient();
  const [stage, setStage] = useState<Stage>({ step: "setup" });
  const [choice, setChoice] = useState<Omit<LoginChoice, "countryCode"> & { countryCode: string | null }>({
    countryCode: null,
    name: "",
    monitorExisting: true,
    deregisterPrevious: true,
  });
  const marketplaces = useMarketplaces();
  const start = useStartLogin(purpose);

  // An account's own marketplace is fixed; the migration's placeholder has none, so any goes
  const fixed = purpose.kind === "relogin" && purpose.account.country_code ? purpose.account.country_code : null;
  const countryCode =
    fixed ?? choice.countryCode ?? defaultMarketplace(marketplaces.data ?? [], globalThis.navigator?.languages ?? []);

  const begin = (method: "code" | "browser") => {
    start.mutate(
      { method, choice: { ...choice, countryCode } },
      { onSuccess: (login) => setStage({ step: method, login }) },
    );
  };

  const finished = (account: Account | null) => {
    // A new account brings a library; a sign-in again queues the refused books
    void refreshAfterAccountChange(client, { books: true });
    setStage({ step: "done", account });
  };

  // A login started here may have become an account the dialog never heard of: a code
  // confirmed after the last poll, or a pasted address finishing after the close
  const close = () => {
    if (stage.step !== "setup") void refreshAfterAccountChange(client, { books: true });
    onClose();
  };

  const index = stepIndex(stage);
  const startError = start.error ? `Could not start the sign-in: ${errorDetail(start.error)}` : null;

  return (
    <Dialog open onClose={close} title={dialogTitle(purpose)} width={520} closeButton>
      <ol aria-label="Steps" className={styles.steps}>
        {stepLabels(purpose).map((label, i) => (
          <li
            key={label}
            className={i <= index ? styles.stepOn : undefined}
            aria-current={i === index ? "step" : undefined}
          >
            <span className={styles.stepBar} />
            {label}
          </li>
        ))}
      </ol>

      {stage.step === "setup" && (
        <form
          className={styles.stack}
          onSubmit={(event: FormEvent) => {
            event.preventDefault();
            begin("code");
          }}
        >
          {fixed ? (
            <FixedMarketplace label={marketplaceName(marketplaces.data, fixed)} />
          ) : (
            <div className="field">
              <label htmlFor="marketplace">Marketplace</label>
              <select
                id="marketplace"
                className="input"
                value={countryCode}
                onChange={(event) => setChoice({ ...choice, countryCode: event.target.value })}
                disabled={!marketplaces.data}
              >
                {marketplaces.data?.map((marketplace) => (
                  <option key={marketplace.country_code} value={marketplace.country_code}>
                    {marketplaceLabel(marketplace)}
                  </option>
                ))}
              </select>
              {marketplaces.error && (
                <span className="field-error">Could not load the marketplaces: {errorDetail(marketplaces.error)}</span>
              )}
            </div>
          )}

          {purpose.kind === "add" ? (
            <>
              <div className="field">
                <label htmlFor="account-name">
                  Name <span className={styles.optional}>(optional)</span>
                </label>
                <input
                  id="account-name"
                  className="input"
                  type="text"
                  maxLength={100}
                  placeholder="Defaults to your Amazon name and country"
                  value={choice.name}
                  onChange={(event) => setChoice({ ...choice, name: event.target.value })}
                />
              </div>
              <CheckCard
                checked={choice.monitorExisting}
                onChange={(monitorExisting) => setChoice({ ...choice, monitorExisting })}
                title="Download the books already in this library"
                hint="Off: they are added unmonitored, and you choose which to download."
              />
            </>
          ) : (
            <>
              <div className={styles.keptNote}>
                <Icon name="check" size={16} strokeWidth={2.4} />
                <span>
                  Your library and download history are kept. Books Audible refused to license are queued again once you
                  have signed in.
                </span>
              </div>
              {!purpose.account.needs_login && (
                <CheckCard
                  checked={choice.deregisterPrevious}
                  onChange={(deregisterPrevious) => setChoice({ ...choice, deregisterPrevious })}
                  title="Remove the old device from my Amazon account"
                  hint="Recommended. The old registration is the one Audible stopped licensing."
                />
              )}
            </>
          )}

          {startError && (
            <p role="alert" className="alert-error">
              {startError}
            </p>
          )}
          <button type="submit" className="btn btn-primary btn-tall" disabled={start.isPending || !countryCode}>
            {start.isPending ? "Asking Amazon for a code…" : "Get sign-in code"}
          </button>
        </form>
      )}

      {stage.step === "code" && (
        <CodeStep
          key={stage.login.login_id}
          login={stage.login}
          starting={start.isPending}
          startError={startError}
          onNewCode={() => begin("code")}
          onBrowser={() => begin("browser")}
          onConfirmed={finished}
        />
      )}

      {stage.step === "browser" && (
        <BrowserStep
          key={stage.login.login_id}
          login={stage.login}
          starting={start.isPending}
          startError={startError}
          onRestart={() => begin("browser")}
          onDone={finished}
        />
      )}

      {stage.step === "done" && <DoneStep purpose={purpose} account={stage.account} onClose={close} />}
    </Dialog>
  );
}

function marketplaceName(marketplaces: readonly Marketplace[] | undefined, code: string) {
  const marketplace = marketplaces?.find((entry) => entry.country_code === code);
  return marketplace ? marketplaceLabel(marketplace) : code.toUpperCase();
}

function FixedMarketplace({ label }: { label: string }) {
  return (
    <div className="field">
      <span className="label">Marketplace</span>
      <div className={styles.locked}>
        <Icon name="lock" size={15} />
        {label}
      </div>
      <span className="hint">Fixed to this account's library. To use another marketplace, add a new account.</span>
    </div>
  );
}

function FailedHead({ error }: { error: string }) {
  return (
    <div className={styles.alertHead}>
      <Icon name="warning" size={18} />
      <span>
        <span className={styles.failedTitle}>Sign-in failed</span>
        <code className={styles.errorText}>{error}</code>
      </span>
    </div>
  );
}

function CheckCard({
  checked,
  onChange,
  title,
  hint,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  title: string;
  hint: string;
}) {
  return (
    <label className={styles.checkCard}>
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      <span>
        <span className={styles.checkTitle}>{title}</span>
        <span className={styles.checkHint}>{hint}</span>
      </span>
    </label>
  );
}

/** The time now, ticking every second while mounted: the countdown's clock. */
function useNow(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  return now;
}

function CodeStep({
  login,
  starting,
  startError,
  onNewCode,
  onBrowser,
  onConfirmed,
}: {
  login: Login;
  starting: boolean;
  startError: string | null;
  onNewCode: () => void;
  onBrowser: () => void;
  onConfirmed: (account: Account | null) => void;
}) {
  const toast = useToast();
  const poll = useLoginStatus(login);
  const now = useNow();
  const current = poll.data ?? login;
  // A 404: the service has forgotten the login, so the code is no use any more
  const forgotten = isForgotten(poll.error);
  const state: CodeState = forgotten ? "expired" : current.status;
  const code = current.code ?? "";
  const address = current.verification_url ?? "";

  // A confirmed login has already become the account; say so once
  const reported = useRef(false);
  useEffect(() => {
    if (state !== "confirmed" || reported.current) return;
    reported.current = true;
    onConfirmed(current.account ?? null);
  }, [state, current.account, onConfirmed]);

  const copy = navigator.clipboard
    ? () =>
        navigator.clipboard.writeText(code).then(
          () => toast.success("Code copied."),
          (error) => toast.error("Could not copy the code", error),
        )
    : null;

  return (
    <div className={styles.stack}>
      <p className={styles.lead}>
        On any device, phone included, go to{" "}
        <a href={address} target="_blank" rel="noreferrer">
          {shortAddress(address)}
        </a>
        , sign in if asked, and enter this code:
      </p>
      <div className={`${styles.code} ${state === "pending" ? "" : styles.codeDead}`}>
        <span className="visually-hidden">Code: {code}</span>
        {[...code].map((letter, i) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: a code's letters can repeat
          <span key={i} aria-hidden="true" className={state === "expired" ? styles.struck : undefined}>
            {letter}
          </span>
        ))}
      </div>

      {state === "pending" && (
        <>
          <div className={styles.codeActions}>
            {copy && (
              <button type="button" className="btn btn-secondary btn-small" onClick={copy}>
                Copy code
              </button>
            )}
            <a className="btn btn-secondary btn-small" href={address} target="_blank" rel="noreferrer">
              Open {shortAddress(address)}
            </a>
          </div>
          <div role="status" className={styles.waiting}>
            <Icon name="spinner" size={18} strokeWidth={2.4} className={styles.spin} />
            <span>Waiting for you to enter the code…</span>
            <span className={styles.countdown}>{remainingText(current.expires_at, now)}</span>
          </div>
          <div className={styles.fallback}>
            <span>Code refused? Older Audible accounts may need</span>
            <button type="button" className="link-btn" onClick={onBrowser} disabled={starting}>
              browser sign-in
            </button>
          </div>
        </>
      )}

      {state === "expired" && (
        <div role="alert" className={styles.expired}>
          <div className={styles.alertHead}>
            <Icon name="clock" size={18} />
            <span>
              <span className={styles.alertTitle}>This code has expired</span>
              <span className={styles.alertText}>
                A code lasts ten minutes. Get a new one and enter it at {shortAddress(address)}.
              </span>
            </span>
          </div>
          <button type="button" className="btn btn-primary" onClick={onNewCode} disabled={starting}>
            {starting ? "Asking Amazon…" : "Get a new code"}
          </button>
        </div>
      )}

      {state === "failed" && (
        <div role="alert" className={styles.failed}>
          <FailedHead error={current.error ?? "Amazon did not say why."} />
          <div className={styles.codeActions}>
            <button type="button" className="btn btn-primary" onClick={onNewCode} disabled={starting}>
              Try again
            </button>
            <button type="button" className="btn btn-secondary" onClick={onBrowser} disabled={starting}>
              Use browser sign-in
            </button>
          </div>
        </div>
      )}

      {startError && (
        <p role="alert" className="alert-error">
          {startError}
        </p>
      )}
    </div>
  );
}

function BrowserStep({
  login,
  starting,
  startError,
  onRestart,
  onDone,
}: {
  login: Login;
  starting: boolean;
  startError: string | null;
  onRestart: () => void;
  onDone: (account: Account | null) => void;
}) {
  const [address, setAddress] = useState("");
  const complete = useCompleteLogin();
  const error = complete.error instanceof ApiError ? complete.error : null;
  // 400: no code in that address - the login stays open for the right one
  const pasteAgain = error?.status === 400 ? error.detail : null;
  // 404 (expired, finished) or 502 (Amazon rejected it): this login is over
  const over = error && (error.status === 404 || error.status === 502) ? error.detail : null;
  // Anything else (no answer, a 5xx on the way) says nothing about the login: try again
  const retry = complete.error && !pasteAgain && !over ? errorDetail(complete.error) : null;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    complete.mutate(
      { loginId: login.login_id, responseUrl: address.trim() },
      { onSuccess: (finished) => onDone(finished.account ?? null) },
    );
  };

  return (
    <form className={styles.stack} onSubmit={submit}>
      <div className={styles.numbered}>
        <span className={styles.number}>1</span>
        <div className={styles.stack}>
          <span>Sign in to Amazon in a new tab.</span>
          <a
            className={`btn btn-secondary ${styles.start}`}
            href={login.url ?? undefined}
            target="_blank"
            rel="noreferrer"
          >
            Open Amazon sign-in
          </a>
        </div>
      </div>
      <div className={styles.numbered}>
        <span className={styles.number}>2</span>
        <div className="field">
          <label htmlFor="response-url">
            You land on a "page not found" page. Copy its full address and paste it here.
          </label>
          <input
            id="response-url"
            className={`input ${styles.mono}`}
            type="url"
            required
            placeholder="https://www.amazon.co.uk/ap/maplanding?..."
            value={address}
            onChange={(event) => setAddress(event.target.value)}
            aria-invalid={pasteAgain ? true : undefined}
            aria-describedby={pasteAgain ? "response-url-error" : undefined}
          />
          {pasteAgain && (
            <span id="response-url-error" className="field-error">
              {pasteAgain}
            </span>
          )}
        </div>
      </div>
      {over ? (
        <div role="alert" className={styles.failed}>
          <FailedHead error={over} />
          <button type="button" className="btn btn-primary" onClick={onRestart} disabled={starting}>
            Start again
          </button>
        </div>
      ) : (
        <>
          {retry && (
            <p role="alert" className="alert-error">
              Could not finish the sign-in: {retry} Try Finish sign-in again.
            </p>
          )}
          <button type="submit" className="btn btn-primary btn-tall" disabled={complete.isPending || !address.trim()}>
            {complete.isPending ? "Finishing…" : "Finish sign-in"}
          </button>
        </>
      )}
      {startError && (
        <p role="alert" className="alert-error">
          {startError}
        </p>
      )}
    </form>
  );
}

function DoneStep({ purpose, account, onClose }: { purpose: Purpose; account: Account | null; onClose: () => void }) {
  const { start } = useSyncActions();
  const { title, text } = doneText(purpose, account);
  return (
    <div className={styles.done}>
      <span className={styles.doneMark}>
        <Icon name="check" size={28} strokeWidth={2.6} />
      </span>
      <div className={styles.doneText}>
        <span className={styles.doneTitle}>{title}</span>
        <span className="muted">{text}</span>
      </div>
      <div className={styles.codeActions}>
        <button type="button" className="btn btn-secondary" onClick={onClose}>
          Done
        </button>
        <button
          type="button"
          className="btn btn-primary"
          disabled={start.isPending}
          onClick={() => start.mutate(undefined, { onSettled: onClose })}
        >
          Sync now
        </button>
      </div>
    </div>
  );
}
