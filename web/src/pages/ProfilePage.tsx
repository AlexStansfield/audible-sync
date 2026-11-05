/**
 * Your sign-in (Profile.dc.html): the web app's username and password. Both changes
 * need the current password; a password change signs out every other browser.
 */
import { useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useId, useState } from "react";
import { ApiError, api, unwrap } from "../api/client";
import { keys, useMe } from "../api/queries";
import { signInError } from "../auth/LoginPage";
import { ErrorCard, errorDetail } from "../components/ErrorCard";
import { Icon } from "../components/Icon";
import { SkeletonCard } from "../components/Skeleton";
import { useToast } from "../components/Toasts";
import {
  MAX_PASSWORD_LENGTH,
  MIN_PASSWORD_LENGTH,
  passwordStrength,
  STRENGTH_COLOUR,
  STRENGTH_LABEL,
} from "../lib/password";
import styles from "./ProfilePage.module.css";

const WRONG_CURRENT = "The current password is wrong.";

/** A failed change: the field it belongs to, or the form as a whole. */
export function changeError(error: unknown): { current?: string; form?: string } {
  if (error instanceof ApiError) {
    if (error.status === 403) return { current: WRONG_CURRENT };
    if (error.status === 429) return { form: signInError(error) };
  }
  return { form: errorDetail(error) };
}

export function ProfilePage() {
  const me = useMe();

  return (
    <>
      <header className={styles.header}>
        <h1 className="page-title">Your sign-in</h1>
        <span className="page-subtitle">
          The username and password for this web app. Your Audible sign-ins are under Accounts.
        </span>
      </header>
      {me.isPending && <SkeletonCard rows={2} label="Loading your sign-in" />}
      {me.isError && !me.data && (
        <ErrorCard title="Could not load your sign-in" error={me.error} onRetry={() => void me.refetch()} />
      )}
      {me.data && (
        <>
          {me.data.generated_password && (
            <div role="status" className={`banner-warn ${styles.width}`}>
              <Icon name="lock" size={20} />
              <div className={styles.bannerText}>
                <span className={styles.strong}>You are using the generated password</span>
                <span className={styles.secondary}>
                  It was shown in the service log on the first start. Choose your own below.
                </span>
              </div>
            </div>
          )}
          <UsernameForm current={me.data.username} />
          <PasswordForm />
        </>
      )}
    </>
  );
}

function UsernameForm({ current }: { current: string }) {
  const ids = useId();
  const client = useQueryClient();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<{ current?: string; form?: string }>({});
  const [saved, setSaved] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setErrors({});
    setSaved(false);
    try {
      const me = await unwrap(
        api.PUT("/api/auth/username", { body: { username: username.trim(), current_password: password } }),
      );
      client.setQueryData(keys.me, me);
      setUsername("");
      setPassword("");
      setSaved(true);
    } catch (error) {
      setErrors(changeError(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form aria-labelledby={`${ids}-h`} className={`card ${styles.form} ${styles.width}`} onSubmit={submit} noValidate>
      <div className={styles.formHead}>
        <h2 id={`${ids}-h`} className={styles.formTitle}>
          Username
        </h2>
        <p className={styles.formText}>
          Currently <strong className={styles.strong}>{current}</strong>.
        </p>
      </div>
      {errors.form && (
        <p role="alert" className="alert-error">
          {errors.form}
        </p>
      )}
      <div className={styles.pair}>
        <div className="field">
          <label htmlFor={`${ids}-name`}>New username</label>
          <input
            id={`${ids}-name`}
            className="input"
            type="text"
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            maxLength={64}
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />
        </div>
        <PasswordField
          id={`${ids}-pw`}
          label="Current password"
          autoComplete="current-password"
          value={password}
          onChange={setPassword}
          error={errors.current}
        />
      </div>
      <div className={styles.actions}>
        <button type="submit" className="btn btn-primary" disabled={busy || !username.trim() || !password}>
          Change username
        </button>
        {saved && (
          <span role="status" className={styles.saved}>
            Username changed. Use it next time you sign in.
          </span>
        )}
      </div>
    </form>
  );
}

function PasswordForm() {
  const ids = useId();
  const client = useQueryClient();
  const toast = useToast();
  const [current, setCurrent] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [confirmTouched, setConfirmTouched] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<{ current?: string; form?: string }>({});

  const tooShort = password.length > 0 && password.length < MIN_PASSWORD_LENGTH;
  const mismatch = confirm.length > 0 && confirm !== password && (confirmTouched || submitted);
  const strength = passwordStrength(password);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitted(true);
    if (password.length < MIN_PASSWORD_LENGTH || password !== confirm) return;
    setBusy(true);
    setErrors({});
    try {
      const me = await unwrap(
        api.PUT("/api/auth/password", { body: { current_password: current, new_password: password } }),
      );
      client.setQueryData(keys.me, me);
      setCurrent("");
      setPassword("");
      setConfirm("");
      setConfirmTouched(false);
      setSubmitted(false);
      toast.success("Password changed. Other browsers are signed out; this one stays signed in.");
    } catch (error) {
      setErrors(changeError(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form aria-labelledby={`${ids}-h`} className={`card ${styles.form} ${styles.width}`} onSubmit={submit} noValidate>
      <div className={styles.formHead}>
        <h2 id={`${ids}-h`} className={styles.formTitle}>
          Password
        </h2>
        <p className={styles.formText}>
          Other browsers are signed out when the password changes. This one stays signed in.
        </p>
      </div>
      {errors.form && (
        <p role="alert" className="alert-error">
          {errors.form}
        </p>
      )}
      <div className={styles.single}>
        <PasswordField
          id={`${ids}-current`}
          label="Current password"
          autoComplete="current-password"
          value={current}
          onChange={setCurrent}
          error={errors.current}
        />
      </div>
      <div className={styles.pair}>
        <div className="field">
          <label htmlFor={`${ids}-new`}>New password</label>
          <input
            id={`${ids}-new`}
            className="input"
            type="password"
            autoComplete="new-password"
            maxLength={MAX_PASSWORD_LENGTH}
            aria-describedby={`${ids}-hint`}
            aria-invalid={submitted && tooShort ? true : undefined}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
          <div className={styles.meter} aria-hidden="true">
            {[1, 2, 3, 4].map((step) => (
              <span key={step} style={{ background: strength >= step ? STRENGTH_COLOUR[strength] : "var(--line)" }} />
            ))}
          </div>
          <span id={`${ids}-hint`} className={submitted && tooShort ? "field-error" : "hint"}>
            At least {MIN_PASSWORD_LENGTH} characters. A few random words works well.
            {strength > 0 && <span className={styles.strengthText}> Strength: {STRENGTH_LABEL[strength]}.</span>}
          </span>
        </div>
        <div className="field">
          <label htmlFor={`${ids}-confirm`}>Confirm new password</label>
          <input
            id={`${ids}-confirm`}
            className="input"
            type="password"
            autoComplete="new-password"
            maxLength={MAX_PASSWORD_LENGTH}
            aria-invalid={mismatch ? true : undefined}
            aria-describedby={mismatch ? `${ids}-mismatch` : undefined}
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
            onBlur={() => setConfirmTouched(true)}
          />
          {mismatch && (
            <span id={`${ids}-mismatch`} className="field-error">
              The two passwords do not match.
            </span>
          )}
        </div>
      </div>
      <div className={styles.actions}>
        <button type="submit" className="btn btn-primary" disabled={busy || !current || !password || !confirm}>
          Change password
        </button>
      </div>
    </form>
  );
}

function PasswordField({
  id,
  label,
  autoComplete,
  value,
  onChange,
  error,
}: {
  id: string;
  label: string;
  autoComplete: string;
  value: string;
  onChange: (value: string) => void;
  error?: string;
}) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        className="input"
        type="password"
        autoComplete={autoComplete}
        maxLength={MAX_PASSWORD_LENGTH}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-error` : undefined}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
      {error && (
        <span id={`${id}-error`} className="field-error">
          {error}
        </span>
      )}
    </div>
  );
}
