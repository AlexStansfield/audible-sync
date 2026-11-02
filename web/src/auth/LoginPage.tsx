/**
 * Sign in (Login.dc.html): username, password, Keep me signed in. A wrong username or
 * password is one message that does not say which; too many attempts says how long to
 * wait. "Forgot your password?" swaps the form for the reset command.
 */
import { useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { ApiError, api, unwrap } from "../api/client";
import { keys, type Me } from "../api/queries";
import { errorDetail } from "../components/ErrorCard";
import { Icon } from "../components/Icon";
import { safeNext } from "../lib/next";
import { MAX_PASSWORD_LENGTH } from "../lib/password";
import styles from "./auth.module.css";
import { useForgetSession } from "./RequireAuth";

export const RESET_COMMAND = "docker compose exec audible-sync uv run --no-sync python -m src.main reset-password";

/** "Try again in 4 minutes" from a Retry-After of seconds. */
export function waitText(retryAfter: string | null): string {
  const seconds = Number(retryAfter);
  if (!Number.isFinite(seconds) || seconds <= 0) return "Try again later.";
  if (seconds < 60) return `Try again in ${Math.ceil(seconds)} seconds.`;
  const minutes = Math.ceil(seconds / 60);
  return `Try again in ${minutes} minute${minutes === 1 ? "" : "s"}.`;
}

export function signInError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return "Wrong username or password.";
    if (error.status === 429) return `Too many failed attempts. ${waitText(error.headers.get("Retry-After"))}`;
  }
  return errorDetail(error);
}

export function LoginPage() {
  const [params] = useSearchParams();
  const next = safeNext(params.get("next"));
  const navigate = useNavigate();
  const client = useQueryClient();
  const forgetSession = useForgetSession();
  const [forgot, setForgot] = useState(false);

  const nextRef = useRef(next);
  nextRef.current = next;

  // On arrival: whatever an earlier session loaded goes, and a browser still signed in
  // goes on. Once, not again when `next` changes
  useEffect(() => {
    forgetSession();
    let current = true;
    api.GET("/api/auth/me").then(
      ({ data }) => {
        if (current && data) {
          client.setQueryData(keys.me, data);
          navigate(nextRef.current, { replace: true });
        }
      },
      () => undefined,
    );
    return () => {
      current = false;
    };
  }, [forgetSession, client, navigate]);

  useEffect(() => {
    document.title = "Sign in · Audible Sync";
  }, []);

  return (
    <div className={styles.centred}>
      <main className={styles.column}>
        <div className={styles.heading}>
          <span className={styles.bigLogo}>
            <Icon name="logo" size={28} strokeWidth={2.2} />
          </span>
          <h1>Sign in to Audible Sync</h1>
        </div>
        {forgot ? (
          <ForgotCard onBack={() => setForgot(false)} />
        ) : (
          <SignInForm
            onForgot={() => setForgot(true)}
            onSignedIn={(me) => {
              client.setQueryData(keys.me, me);
              navigate(next, { replace: true });
            }}
          />
        )}
        <p className={styles.hintLine}>
          First start? Sign in as <strong>admin</strong> with the password shown in the service log.
        </p>
      </main>
    </div>
  );
}

function SignInForm({ onForgot, onSignedIn }: { onForgot: () => void; onSignedIn: (me: Me) => void }) {
  const ids = useId();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [shown, setShown] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onSignedIn(await unwrap(api.POST("/api/auth/login", { body: { username, password, remember } })));
    } catch (failure) {
      setError(signInError(failure));
      setBusy(false);
    }
  }

  const invalid = error ? true : undefined;
  return (
    <form className={styles.panel} onSubmit={submit} noValidate>
      {error && (
        <p role="alert" className="alert-error">
          {error}
        </p>
      )}
      <div className="field">
        <label htmlFor={`${ids}-user`}>Username</label>
        <input
          id={`${ids}-user`}
          className="input"
          type="text"
          autoComplete="username"
          autoCapitalize="none"
          spellCheck={false}
          required
          aria-invalid={invalid}
          value={username}
          onChange={(event) => setUsername(event.target.value)}
        />
      </div>
      <div className="field">
        <div className={styles.labelRow}>
          <label htmlFor={`${ids}-pw`} className="label">
            Password
          </label>
          <button type="button" className="link-btn" style={{ fontSize: 13 }} onClick={onForgot}>
            Forgot your password?
          </button>
        </div>
        <div className={styles.passwordBox}>
          <input
            id={`${ids}-pw`}
            className="input"
            type={shown ? "text" : "password"}
            autoComplete="current-password"
            required
            maxLength={MAX_PASSWORD_LENGTH}
            aria-invalid={invalid}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
          <button
            type="button"
            className={styles.reveal}
            aria-label={shown ? "Hide password" : "Show password"}
            aria-pressed={shown}
            onClick={() => setShown(!shown)}
          >
            <Icon name={shown ? "eyeOff" : "eye"} />
          </button>
        </div>
      </div>
      <label className="checkbox">
        <input type="checkbox" checked={remember} onChange={(event) => setRemember(event.target.checked)} />
        Keep me signed in
      </label>
      <button type="submit" className="btn btn-primary btn-tall" disabled={busy || !username || !password}>
        {busy ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}

function ForgotCard({ onBack }: { onBack: () => void }) {
  const headingId = useId();
  return (
    <section aria-labelledby={headingId} className={styles.panel} style={{ gap: 16 }}>
      <h2 id={headingId}>Reset your password</h2>
      <p>
        Audible Sync has no email, so a forgotten password is reset on the server it runs on. Run this command there. It
        sets a new password and prints it once in the terminal.
      </p>
      <code className={styles.command}>{RESET_COMMAND}</code>
      <p className={styles.small}>
        Outside Docker it is <code>python -m src.main reset-password</code>. Then sign in with the new password and
        choose your own under Your sign-in. Every browser, this one included, is signed out. Add <code>--username</code>{" "}
        to put the username back to admin as well.
      </p>
      <button type="button" className="btn btn-secondary btn-tall" onClick={onBack}>
        Back to sign in
      </button>
    </section>
  );
}
