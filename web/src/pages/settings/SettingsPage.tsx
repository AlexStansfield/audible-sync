/**
 * Settings (Settings.dc.html): the service's settings as one form saved with the
 * changed fields only, the theme and debug logging, which apply at once, the API token
 * and what the service is running with.
 */
import { type FormEvent, type ReactNode, useEffect, useId, useMemo, useRef, useState } from "react";
import { useBlocker } from "react-router";
import { ApiError } from "../../api/client";
import { useStatus } from "../../api/queries";
import { ConfirmDialog } from "../../components/Dialog";
import { ErrorCard, errorDetail } from "../../components/ErrorCard";
import { SkeletonCard } from "../../components/Skeleton";
import { Switch } from "../../components/Switch";
import { useToast } from "../../components/Toasts";
import { formatBytes } from "../../lib/format";
import type { ThemeChoice } from "../../lib/theme";
import { useTheme } from "../../lib/useTheme";
import { useStats } from "../dashboard/queries";
import {
  changedFields,
  type Draft,
  draftFrom,
  type ErrorField,
  type Errors,
  fieldOfError,
  type Settings,
  switchUnit,
  templateErrorField,
  type Unit,
  updateFrom,
} from "./form";
import { type NamingBook, outputPath, PLACEHOLDERS, templateError } from "./naming";
import { useApiToken, useRegenerateToken, useSaveSettings, useSettings, useSystem, useWebhookTest } from "./queries";
import styles from "./Settings.module.css";
import { averageBookBytes, diskFree, diskNote, diskUsedPercent, ffmpegText, scheduleText, unsavedText } from "./text";

const FORM_ID = "settings-form";

export function SettingsPage() {
  const settings = useSettings();
  return (
    <>
      {settings.isPending && (
        <>
          <PageHead />
          <SkeletonCard rows={4} label="Loading the settings" />
        </>
      )}
      {settings.isError && !settings.data && (
        <>
          <PageHead />
          <ErrorCard
            title="Could not load the settings"
            error={settings.error}
            onRetry={() => void settings.refetch()}
          />
        </>
      )}
      {settings.data && <SettingsForm settings={settings.data} />}
    </>
  );
}

function PageHead({ children }: { children?: ReactNode }) {
  return (
    <header className={`page-header ${styles.header}`}>
      <div className={styles.titles}>
        <h1 className="page-title">Settings</h1>
        <span className="page-subtitle">Changes apply from the next run. No restart needed.</span>
      </div>
      {children}
    </header>
  );
}

type Set = (patch: Partial<Draft>) => void;

/** The form: what is saved, overlaid with what has been edited and not saved yet. */
function SettingsForm({ settings }: { settings: Settings }) {
  const toast = useToast();
  const save = useSaveSettings();
  const saved = useMemo(() => draftFrom(settings), [settings]);
  const [changes, setChanges] = useState<Partial<Draft>>({});
  const [errors, setErrors] = useState<Errors>({});
  const draft: Draft = { ...saved, ...changes };
  const changed = changedFields(draft, saved);
  const dirty = changed.length > 0;

  const set: Set = (patch) => {
    setChanges((current) => ({ ...current, ...patch }));
    setErrors((current) => {
      const next = { ...current, form: undefined };
      for (const key of Object.keys(patch)) delete next[(key === "unit" ? "interval" : key) as ErrorField];
      return next;
    });
  };

  function discard() {
    setChanges({});
    setErrors({});
  }

  function submit(event?: FormEvent) {
    event?.preventDefault();
    if (!dirty || save.isPending) return;
    const { update, errors: found } = updateFrom(draft, saved);
    if (Object.keys(found).length) {
      setErrors(found);
      return;
    }
    save.mutate(update, {
      onSuccess: () => {
        setChanges({});
        setErrors({});
        toast.success("Settings saved.");
      },
      onError: (error) => {
        if (error instanceof ApiError && error.status === 422) {
          setErrors({ [fieldOfError(error.detail, draft, changed)]: error.detail });
        } else {
          setErrors({ form: errorDetail(error) });
        }
      },
    });
  }

  // Leaving with unsaved changes asks first, in the app and when the tab is closed
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) => dirty && currentLocation.pathname !== nextLocation.pathname,
  );
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  return (
    <>
      <PageHead>
        {dirty && (
          <div className={styles.saveBar}>
            <span className={styles.unsaved}>{unsavedText(changed.length)}</span>
            <button type="button" className="btn btn-secondary" onClick={discard} disabled={save.isPending}>
              Discard
            </button>
            <button type="submit" form={FORM_ID} className="btn btn-primary" disabled={save.isPending}>
              {save.isPending ? "Saving…" : "Save changes"}
            </button>
          </div>
        )}
      </PageHead>
      {errors.form && (
        <p role="alert" className={`alert-error ${styles.width}`}>
          Could not save the settings: {errors.form}
        </p>
      )}
      <form id={FORM_ID} className={styles.form} onSubmit={submit} noValidate>
        <Appearance />
        <Schedule draft={draft} set={set} errors={errors} edited={changed.some(isScheduleField)} />
        <Downloads draft={draft} set={set} errors={errors} />
        <AudioFormat draft={draft} set={set} errors={errors} />
        <Naming draft={draft} set={set} errors={errors} />
        <Notifications draft={draft} set={set} errors={errors} />
      </form>
      <div className={styles.form}>
        <ApiAccess />
        <System />
        <Advanced debug={settings.debug} />
      </div>
      <ConfirmDialog
        open={blocker.state === "blocked"}
        title="Leave without saving?"
        confirmLabel="Leave without saving"
        cancelLabel="Stay on this page"
        onConfirm={() => blocker.proceed?.()}
        onCancel={() => blocker.reset?.()}
      >
        <p className={styles.dialogText}>{unsavedText(changed.length)} on this page will be lost.</p>
      </ConfirmDialog>
    </>
  );
}

const isScheduleField = (field: ErrorField) => field === "interval" || field === "sync_enabled";

function Section({ title, description, children }: { title: string; description?: ReactNode; children: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className={styles.section}>
      <div className={styles.about}>
        <h2 id={id} className={styles.sectionTitle}>
          {title}
        </h2>
        {description && <p className={styles.description}>{description}</p>}
      </div>
      <div className={styles.fields}>{children}</div>
    </section>
  );
}

/** A labelled input with its hint, or its error in the hint's place. */
function Field({
  label,
  hint,
  error,
  children,
}: {
  label: ReactNode;
  hint?: ReactNode;
  error?: string | null;
  children: (props: { id: string; "aria-invalid"?: true; "aria-describedby"?: string }) => ReactNode;
}) {
  const id = useId();
  const noteId = `${id}-note`;
  const note = error || hint;
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children({
        id,
        "aria-invalid": error ? true : undefined,
        "aria-describedby": note ? noteId : undefined,
      })}
      {note && (
        <span id={noteId} className={error ? "field-error" : "hint"}>
          {note}
        </span>
      )}
    </div>
  );
}

function SwitchRow({
  label,
  hint,
  checked,
  onChange,
  disabled,
}: {
  label: string;
  hint: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <div className={styles.switchRow}>
      <Switch checked={checked} onChange={onChange} labelledBy={id} disabled={disabled} />
      <span className={styles.switchText}>
        <span id={id} className={styles.switchLabel}>
          {label}
        </span>
        <span className={styles.switchHint}>{hint}</span>
      </span>
    </div>
  );
}

const THEMES: { id: ThemeChoice; label: string; swatch: string | undefined }[] = [
  { id: "system", label: "System", swatch: styles.swatchSystem },
  { id: "light", label: "Light", swatch: styles.swatchLight },
  { id: "dark", label: "Dark", swatch: styles.swatchDark },
];

function Appearance() {
  const [choice, setChoice] = useTheme();
  return (
    <Section title="Appearance" description="Saved in this browser. Applies straight away.">
      <fieldset className={styles.plainFieldset}>
        <legend className="label">Theme</legend>
        <div className={styles.themes}>
          {THEMES.map((theme) => (
            <label key={theme.id} className={styles.themeCard}>
              <input
                type="radio"
                name="theme"
                className="visually-hidden"
                checked={choice === theme.id}
                onChange={() => setChoice(theme.id)}
              />
              <span aria-hidden="true" className={`${styles.swatch} ${theme.swatch}`}>
                <span className={styles.swatchSide} />
                <span className={styles.swatchCard} />
              </span>
              {theme.label}
            </label>
          ))}
        </div>
      </fieldset>
      <span className="hint">System follows your device's light or dark setting.</span>
    </Section>
  );
}

interface SectionProps {
  draft: Draft;
  set: Set;
  errors: Errors;
}

function Schedule({ draft, set, errors, edited }: SectionProps & { edited: boolean }) {
  const status = useStatus();
  return (
    <Section title="Schedule" description="When the service checks your libraries and downloads what is waiting.">
      <SwitchRow
        label="Run on a schedule"
        hint="Off: runs only start when you press Sync now."
        checked={draft.sync_enabled}
        onChange={(sync_enabled) => set({ sync_enabled })}
      />
      <Field label="Run every" error={errors.interval} hint={scheduleText(status.data?.scheduler, edited)}>
        {(props) => (
          <div className={styles.interval}>
            <input
              {...props}
              type="number"
              inputMode="numeric"
              min={1}
              className={`input ${styles.short}`}
              value={draft.interval}
              onChange={(event) => set({ interval: event.target.value })}
            />
            <select
              aria-label="Unit"
              className={`input ${styles.unit}`}
              value={draft.unit}
              onChange={(event) => set(switchUnit(draft, event.target.value as Unit))}
            >
              <option value="hours">hours</option>
              <option value="minutes">minutes</option>
            </select>
          </div>
        )}
      </Field>
      <SwitchRow
        label="Download new purchases automatically"
        hint="Off: new books arrive unmonitored for you to pick."
        checked={draft.auto_monitor_new}
        onChange={(auto_monitor_new) => set({ auto_monitor_new })}
      />
    </Section>
  );
}

function NumberField({
  label,
  hint,
  field,
  placeholder,
  draft,
  set,
  errors,
}: SectionProps & {
  label: string;
  hint: string;
  field: "max_download" | "max_attempts" | "max_consecutive_failures";
  placeholder?: string;
}) {
  return (
    <Field label={label} hint={hint} error={errors[field]}>
      {(props) => (
        <input
          {...props}
          type="number"
          inputMode="numeric"
          min={1}
          placeholder={placeholder}
          className="input"
          value={draft[field]}
          onChange={(event) => set({ [field]: event.target.value })}
        />
      )}
    </Field>
  );
}

function Downloads(props: SectionProps) {
  return (
    <Section title="Downloads" description="Limits per account, per run.">
      <div className={styles.columns}>
        <NumberField
          {...props}
          field="max_download"
          label="Books per run"
          hint="Empty: everything waiting."
          placeholder="No limit"
        />
        <NumberField {...props} field="max_attempts" label="Attempts per book" hint="Then marked failed." />
        <NumberField
          {...props}
          field="max_consecutive_failures"
          label="Stop after failures in a row"
          hint="Empty: never give up."
          placeholder="Never"
        />
      </div>
    </Section>
  );
}

const FORMATS = [
  {
    id: "m4b",
    title: "M4B (original audio)",
    description: "AAC exactly as Audible supplies it. No re-encoding, no quality loss.",
  },
  {
    id: "oga",
    title: "Ogg Opus",
    description: "Re-encoded. Much smaller for speech at the same perceived quality.",
  },
];

function AudioFormat({ draft, set, errors }: SectionProps) {
  const system = useSystem();
  const oga = draft.encoding_format === "oga";
  return (
    <Section
      title="Audio format"
      description="Applies to books downloaded from now on. Existing files are not converted."
    >
      <fieldset className={styles.plainFieldset}>
        <legend className="label">Format</legend>
        <div className={styles.formats}>
          {FORMATS.map((format) => (
            <label key={format.id} className={styles.formatCard}>
              <input
                type="radio"
                name="encoding_format"
                checked={draft.encoding_format === format.id}
                onChange={() => set({ encoding_format: format.id })}
              />
              <span className={styles.formatText}>
                <span className={styles.formatTitle}>{format.title}</span>
                <span className={styles.description}>{format.description}</span>
              </span>
            </label>
          ))}
        </div>
        {errors.encoding_format && <span className="field-error">{errors.encoding_format}</span>}
      </fieldset>
      {oga && system.data && !system.data.libopus && (
        <p className="alert-error">
          This service's FFmpeg has no libopus encoder, so Ogg Opus downloads will fail. Use M4B, or an FFmpeg built
          with libopus.
        </p>
      )}
      {oga && (
        <Field
          label={
            <>
              Bitrate <span className={styles.bitrate}>{draft.bitrate} kbps</span>
            </>
          }
          hint="64 matches Audible's quality. 32 to 48 is still very good for speech."
          error={errors.bitrate}
        >
          {(props) => (
            <input
              {...props}
              type="range"
              min={1}
              max={256}
              className={styles.range}
              value={draft.bitrate}
              onChange={(event) => set({ bitrate: Number(event.target.value) })}
            />
          )}
        </Field>
      )}
    </Section>
  );
}

/** The preview's two books: one in a series, one not (the mockup's own). */
const SAMPLES: NamingBook[] = [
  {
    asin: "B0711J5TDF",
    title: "One Word Kill",
    authors: ["Mark Lawrence"],
    narrators: ["James Clamp"],
    series: [{ title: "Impossible Times", sequence: "1" }],
    release_date: "2019-05-01",
  },
  {
    asin: "B08G9PRS1K",
    title: "Project Hail Mary",
    authors: ["Andy Weir"],
    narrators: ["Ray Porter"],
    series: [],
    release_date: "2021-05-04",
  },
];

type Template = "folder_template" | "filename_template";

function Naming({ draft, set, errors }: SectionProps) {
  const inputs = { folder_template: useRef<HTMLInputElement>(null), filename_template: useRef<HTMLInputElement>(null) };
  const [target, setTarget] = useState<Template>("folder_template");
  const live = templateError(draft.folder_template, draft.filename_template);
  const extension = draft.encoding_format === "oga" ? "oga" : "m4b";

  function insert(placeholder: string) {
    const input = inputs[target].current;
    const value = draft[target];
    const start = input?.selectionStart ?? value.length;
    const end = input?.selectionEnd ?? value.length;
    set({ [target]: value.slice(0, start) + placeholder + value.slice(end) });
    requestAnimationFrame(() => {
      input?.focus();
      input?.setSelectionRange(start + placeholder.length, start + placeholder.length);
    });
  }

  const templateField = (field: Template, label: string) => {
    // A template the service would refuse is said as soon as it is typed
    const error = errors[field] ?? (live && templateErrorField(live, draft) === field ? live : null);
    return (
      <Field label={label} error={error}>
        {(props) => (
          <input
            {...props}
            ref={inputs[field]}
            type="text"
            spellCheck={false}
            autoComplete="off"
            className={`input ${styles.mono}`}
            value={draft[field]}
            onFocus={() => setTarget(field)}
            onChange={(event) => set({ [field]: event.target.value })}
          />
        )}
      </Field>
    );
  };

  return (
    <Section
      title="Folders and naming"
      description="Where books are filed. Text in [brackets] is left out when a placeholder in it is empty."
    >
      <div className={styles.columns2}>
        {(["audiobook_folder", "download_folder"] as const).map((field) => (
          <Field
            key={field}
            label={field === "audiobook_folder" ? "Audiobooks folder" : "Temporary downloads folder"}
            error={errors[field]}
          >
            {(props) => (
              <input
                {...props}
                type="text"
                spellCheck={false}
                autoComplete="off"
                className={`input ${styles.mono}`}
                value={draft[field]}
                onChange={(event) => set({ [field]: event.target.value })}
              />
            )}
          </Field>
        ))}
      </div>
      {templateField("folder_template", "Folder template")}
      {templateField("filename_template", "File name template")}
      <div className={styles.inserts}>
        <span className="hint">
          Insert into the {target === "folder_template" ? "folder template" : "file name template"}
        </span>
        {PLACEHOLDERS.map((placeholder) => (
          <button
            key={placeholder}
            type="button"
            className={styles.insert}
            // Keep the caret where it is in the template, so the insert lands there
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => insert(`{${placeholder}}`)}
          >
            {`{${placeholder}}`}
          </button>
        ))}
      </div>
      <div className={styles.preview}>
        <span className="hint">Preview</span>
        {live ? (
          <span className="field-error">Fix the template to see where books would go.</span>
        ) : (
          SAMPLES.map((book) => {
            const { segments, stem } = outputPath(book, draft.folder_template, draft.filename_template);
            const root = draft.audiobook_folder.trim().replace(/\/+$/, "");
            return (
              <code key={book.asin} className={styles.path}>
                {[root, ...segments].join("/")}/{stem}.{extension}
              </code>
            );
          })
        )}
        <span className="hint">Books already downloaded keep their paths; files are not renamed.</span>
      </div>
    </Section>
  );
}

function Notifications({ draft, set, errors }: SectionProps) {
  const test = useWebhookTest();
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);

  function send() {
    setResult(null);
    test.mutate(draft.webhook_url.trim(), {
      onSuccess: () => setResult({ ok: true, text: "Test event delivered." }),
      onError: (error) => setResult({ ok: false, text: errorDetail(error) }),
    });
  }

  return (
    <Section
      title="Notifications"
      description="A JSON POST after every run, for example to make Audiobookshelf rescan."
    >
      <Field label="Webhook URL" error={errors.webhook_url}>
        {(props) => (
          <div className={styles.inline}>
            <input
              {...props}
              type="url"
              spellCheck={false}
              placeholder="https://"
              className={`input ${styles.mono}`}
              value={draft.webhook_url}
              onChange={(event) => {
                setResult(null);
                set({ webhook_url: event.target.value });
              }}
            />
            <button type="button" className="btn btn-secondary" onClick={send} disabled={test.isPending}>
              {test.isPending ? "Sending…" : "Send test"}
            </button>
          </div>
        )}
      </Field>
      <span role="status" className={result ? (result.ok ? styles.ok : "field-error") : undefined}>
        {result?.text}
      </span>
    </Section>
  );
}

function ApiAccess() {
  const toast = useToast();
  const token = useApiToken();
  const regenerate = useRegenerateToken();
  const [fresh, setFresh] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const fromEnv = token.data?.source === "env";

  function confirm() {
    regenerate.mutate(undefined, {
      onSuccess: ({ token: value }) => {
        setFresh(value);
        setConfirming(false);
      },
      onError: (error) => {
        setConfirming(false);
        toast.error("Could not regenerate the token", error);
      },
    });
  }

  const copy =
    fresh && navigator.clipboard
      ? () =>
          navigator.clipboard.writeText(fresh).then(
            () => toast.success("Token copied."),
            (error) => toast.error("Could not copy the token", error),
          )
      : null;

  return (
    <Section
      title="API access"
      description="A bearer token for scripts and the command line. The web app does not need it."
    >
      {token.isError && !token.data && (
        <ErrorCard title="Could not load the API token" error={token.error} onRetry={() => void token.refetch()} />
      )}
      {token.isPending && <SkeletonCard rows={1} label="Loading the API token" />}
      {token.data && (
        <>
          <span className="label">API token</span>
          <div className={styles.tokenRow}>
            <code className={`${styles.token} ${fresh ? styles.tokenFresh : ""}`}>
              {fresh ?? `•••••••••••••••••••••••• ${token.data.last4}`}
            </code>
            {fresh && (
              <button type="button" className="btn btn-primary" onClick={copy ?? undefined} disabled={!copy}>
                Copy
              </button>
            )}
            <button
              type="button"
              className="btn btn-danger-outline"
              onClick={() => setConfirming(true)}
              disabled={fromEnv || regenerate.isPending}
            >
              Regenerate
            </button>
          </div>
          {fresh ? (
            <span role="status" className={styles.fresh}>
              Copy it now. Once you leave this page only the last four characters are shown again.
            </span>
          ) : fromEnv ? (
            <span className="hint">
              The token comes from <code className="mono">AUDIBLE_SYNC_API_TOKEN</code>: to change it, change it there
              and restart the service.
            </span>
          ) : (
            <span className="hint">
              Only the last four characters are shown. To copy a token, regenerate it. If the token comes from{" "}
              <code className="mono">AUDIBLE_SYNC_API_TOKEN</code>, change it there instead.
            </span>
          )}
        </>
      )}
      <ConfirmDialog
        open={confirming}
        title="Regenerate the API token?"
        confirmLabel="Regenerate"
        cancelLabel="Keep the current token"
        busy={regenerate.isPending}
        onConfirm={confirm}
        onCancel={() => setConfirming(false)}
      >
        <p className={styles.dialogText}>
          The current token stops working at once. Scripts and command-line tools that use it fail until you give them
          the new one. The web app is not affected.
        </p>
      </ConfirmDialog>
    </Section>
  );
}

function System() {
  const system = useSystem();
  const stats = useStats();
  return (
    <Section title="System" description="What the service is running with. Read only.">
      {system.isPending && <SkeletonCard rows={2} label="Loading the system details" />}
      {system.isError && !system.data && (
        <ErrorCard
          title="Could not load the system details"
          error={system.error}
          onRetry={() => void system.refetch()}
        />
      )}
      {system.data && <SystemDetails system={system.data} average={averageBookBytes(stats.data)} />}
    </Section>
  );
}

function SystemDetails({
  system,
  average,
}: {
  system: NonNullable<ReturnType<typeof useSystem>["data"]>;
  average: number | null;
}) {
  const disk = system.audiobook_folder;
  const used = diskUsedPercent(disk);
  const ffmpeg = ffmpegText(system);
  const note = diskNote(disk, average);
  return (
    <>
      <div className={`${styles.disk} ${disk.low_space ? styles.diskLow : ""}`}>
        <div className={styles.diskHead}>
          <span className={styles.strong}>Audiobooks folder</span>
          <code className={styles.diskPath}>{disk.path}</code>
          <span className={styles.diskFree}>{diskFree(disk)}</span>
        </div>
        {used != null && (
          // biome-ignore lint/a11y/useSemanticElements: a <meter> cannot take the theme's colours in every browser
          <div
            role="meter"
            aria-label="Disk space used"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={used}
            className={styles.meter}
          >
            <div className={styles.meterFill} style={{ width: `${used}%` }} />
          </div>
        )}
        {note && <span className={disk.low_space ? "field-error" : "hint"}>{note}</span>}
      </div>
      <dl className={styles.facts}>
        <div>
          <dt>Audible Sync</dt>
          <dd>{system.version}</dd>
        </div>
        <div>
          <dt>FFmpeg</dt>
          <dd>
            {ffmpeg.version} <span className={ffmpeg.problem ? styles.problem : styles.aside}>· {ffmpeg.note}</span>
          </dd>
        </div>
        <div>
          <dt>Encryption key</dt>
          <dd>
            {system.key_source === "env" ? (
              <>
                Environment variable <code className={styles.diskPath}>AUDIBLE_SYNC_SECRET_KEY</code>
              </>
            ) : (
              <>
                Key file <code className={styles.diskPath}>{system.key_file}</code>
              </>
            )}
          </dd>
        </div>
        <div>
          <dt>Database</dt>
          <dd>{formatBytes(system.database_bytes)}</dd>
        </div>
      </dl>
      <span className="hint">
        Back up the key separately from the database: one without the other is not a working Audible sign-in.
      </span>
    </>
  );
}

/** Debug logging applies at once: its own save, apart from the form's. */
function Advanced({ debug }: { debug: boolean }) {
  const toast = useToast();
  const save = useSaveSettings();
  const shown = save.isPending ? Boolean(save.variables?.debug) : debug;
  return (
    <Section title="Advanced">
      <SwitchRow
        label="Debug logging"
        hint="Applies immediately."
        checked={shown}
        disabled={save.isPending}
        onChange={(value) =>
          save.mutate({ debug: value }, { onError: (error) => toast.error("Could not change debug logging", error) })
        }
      />
    </Section>
  );
}
