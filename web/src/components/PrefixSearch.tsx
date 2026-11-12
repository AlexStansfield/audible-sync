/**
 * The search box with prefixes, on Search and Library (DESIGN.md, "The search box and
 * its prefixes"): the box keeps what was typed and follows the URL; Search tips explain
 * the prefixes; the chips under it show how the box was read, each removable.
 */
import { useEffect, useId, useRef, useState } from "react";
import { FIELD_LABEL, type Part, searchParts, withoutPart } from "../lib/prefixSearch";
import { Icon } from "./Icon";
import styles from "./PrefixSearch.module.css";

/** How long typing pauses before the search runs */
export const SEARCH_DELAY = 300;

const TIPS: { prefix: string; what: string; example: string }[] = [
  { prefix: "author:", what: "books by an author", example: "author:Adrian Tchaikovsky" },
  { prefix: "narrator:", what: "books read by a narrator", example: "narrator:Ray Porter" },
  { prefix: "series:", what: "the books of a series", example: "series:The Expanse" },
  { prefix: "title:", what: "words in the title only", example: "title:Children of Time" },
];

/**
 * The box: the URL follows what is typed once typing pauses, and the box follows the
 * URL when it changes some other way (a chip removed, Clear filters, the back button).
 */
export function PrefixSearchBox({
  id,
  label,
  value,
  onSearch,
  placeholder,
  maxLength,
  helper = true,
}: {
  id: string;
  /** The box's accessible name */
  label: string;
  value: string;
  onSearch: (text: string) => void;
  placeholder: string;
  maxLength?: number;
  /** The helper line under the box naming the prefixes */
  helper?: boolean;
}) {
  const [text, setText] = useState(value);
  const typed = useRef(text);
  typed.current = text;
  const search = useRef(onSearch);
  search.current = onSearch;

  useEffect(() => {
    // A trailing space being typed is not a different search
    if (value !== typed.current.trim()) setText(value);
  }, [value]);

  useEffect(() => {
    const wanted = text.trim();
    if (wanted === value) return;
    const timer = setTimeout(() => search.current(wanted), SEARCH_DELAY);
    return () => clearTimeout(timer);
  }, [text, value]);

  const helperId = useId();

  return (
    <div className={styles.searchWrap}>
      <div className={styles.search}>
        <Icon name="search" size={16} className={styles.searchIcon} />
        <label htmlFor={id} className="visually-hidden">
          {label}
        </label>
        <input
          id={id}
          type="search"
          className={`input ${styles.searchInput}`}
          placeholder={placeholder}
          maxLength={maxLength}
          value={text}
          aria-describedby={helper ? helperId : undefined}
          onChange={(event) => setText(event.target.value)}
        />
        <SearchTips />
      </div>
      {helper && (
        <span id={helperId} className={styles.helper}>
          Narrow it with <code>author:</code> <code>narrator:</code> <code>series:</code> <code>title:</code>
        </span>
      )}
    </div>
  );
}

/** The Search tips button inside the box, and its popover. */
function SearchTips() {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const popoverId = useId();

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent ? event.key === "Escape" : !wrap.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open]);

  return (
    <div ref={wrap} className={styles.tipsWrap}>
      <button
        type="button"
        className={styles.tipsButton}
        aria-expanded={open}
        aria-controls={popoverId}
        onClick={() => setOpen(!open)}
      >
        <Icon name="help" size={15} />
        <span className={styles.tipsLabel}>Search tips</span>
      </button>
      {open && (
        <div id={popoverId} role="dialog" aria-label="Search tips" className={styles.tips}>
          <div className={styles.tipsHead}>
            <span className={styles.tipsTitle}>Search tips</span>
            <button
              type="button"
              className={`icon-btn ${styles.tipsClose}`}
              aria-label="Close search tips"
              onClick={() => setOpen(false)}
            >
              <Icon name="close" size={16} strokeWidth={2.2} />
            </button>
          </div>
          <p className={styles.tipsText}>
            Start a part of the search with a prefix. Its value runs to the next prefix or the end, so no quotes are
            needed; words before the first prefix are keywords.
          </p>
          <dl className={styles.tipsList}>
            {TIPS.map((tip) => (
              <div key={tip.prefix} className={styles.tip}>
                <dt className={styles.prefix}>{tip.prefix}</dt>
                <dd>
                  {tip.what}, <code>{tip.example}</code>
                </dd>
              </div>
            ))}
          </dl>
          <p className={styles.tipsText}>
            Together: <code>colonial author:John Scalzi series:Old Man's War</code>
          </p>
          <p className={styles.tipsNote}>
            Only these four words count, so “Star Wars: Trials of the Jedi” searches as written.
          </p>
        </div>
      )}
    </div>
  );
}

/**
 * "Searching for" and a chip per part of the search, each with ×, and Clear search.
 * `onlyWithPrefix` shows nothing for keywords alone (Library, where plain words are
 * the search they always were).
 */
export function SearchChips({
  text,
  onChange,
  onlyWithPrefix = false,
}: {
  text: string;
  onChange: (text: string) => void;
  onlyWithPrefix?: boolean;
}) {
  const parts = searchParts(text);
  if (parts.length === 0) return null;
  if (onlyWithPrefix && parts.every((part) => part.field === "keywords")) return null;
  return (
    <div className={styles.chipsRow}>
      <span className={styles.chipsLabel} id="searching-for">
        Searching for
      </span>
      <ul className={styles.chips} aria-labelledby="searching-for">
        {parts.map((part) => (
          <li key={part.field}>
            <SearchChip part={part} onRemove={() => onChange(withoutPart(text, part.field))} />
          </li>
        ))}
      </ul>
      <button type="button" className={`link-btn ${styles.clear}`} onClick={() => onChange("")}>
        Clear search
      </button>
    </div>
  );
}

function SearchChip({ part, onRemove }: { part: Part; onRemove: () => void }) {
  const label = FIELD_LABEL[part.field];
  return (
    <span className={`${styles.token} ${part.field === "keywords" ? styles.keywords : ""}`}>
      <span className={styles.tokenField}>{label}</span>
      <span className={styles.tokenValue}>{part.value}</span>
      <button
        type="button"
        className={styles.tokenRemove}
        aria-label={`Remove ${label.toLowerCase()} ${part.value}`}
        onClick={onRemove}
      >
        <Icon name="close" size={12} strokeWidth={2.4} />
      </button>
    </span>
  );
}
