/**
 * A catalogue book as Search, a series, the panel and the Wishlist show it (DESIGN.md,
 * "A catalogue book"): the cover and title (which open the panel), the byline, the
 * series, the facts, the markers, the price and the Add or Remove button.
 */
import { type ReactNode, useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router";
import { Icon } from "../components/Icon";
import { useToast } from "../components/Toasts";
import styles from "./Catalog.module.css";
import { useWishlistChange } from "./queries";
import {
  type Account,
  type CatalogBook,
  factsLine,
  libraryName,
  priceParts,
  seriesLine,
  wishlistChoices,
  wishlistName,
} from "./text";

/** The accounts a book is shown among, and the marketplace searched (`country_code`). */
export type CatalogContext = {
  accounts: Account[];
  countryCode?: string;
  /** The account searched through, first in the Add menu */
  searchedId?: number;
  onOpen: (asin: string) => void;
};

export function CatalogCover({ book, size }: { book: CatalogBook; size: number }) {
  const [failed, setFailed] = useState(false);
  const style = { width: size, height: size };
  if (!book.cover_url || failed) {
    return (
      <span className={`${styles.cover} ${styles.coverPlaceholder}`} style={style}>
        <Icon name="book" size={Math.max(14, size / 3)} />
      </span>
    );
  }
  return (
    <img
      src={book.cover_url}
      alt=""
      loading="lazy"
      decoding="async"
      className={styles.cover}
      style={style}
      onError={() => setFailed(true)}
    />
  );
}

export function BookRow({
  book,
  context,
  coverSize = 88,
  above,
  held = false,
  action,
}: {
  book: CatalogBook;
  context: CatalogContext;
  coverSize?: number;
  /** A line above the title (the Wishlist's account and date) */
  above?: ReactNode;
  /** A book a library holds, shown prominently (the Wishlist) */
  held?: boolean;
  /** In place of the Add or Remove button */
  action?: ReactNode;
}) {
  const facts = factsLine(book);
  const series = seriesLine(book);
  return (
    <article className={`${styles.row} ${held ? styles.held : ""}`} aria-label={book.title}>
      <button
        type="button"
        className={styles.coverButton}
        onClick={() => context.onOpen(book.asin)}
        tabIndex={-1}
        aria-hidden="true"
      >
        <CatalogCover book={book} size={coverSize} />
      </button>
      <div className={styles.main}>
        {above}
        {held && <LibraryMarker book={book} accounts={context.accounts} large />}
        <h3 className={styles.title}>
          <button type="button" className={styles.titleButton} onClick={() => context.onOpen(book.asin)}>
            {book.title}
            {book.subtitle && <span className={styles.subtitle}>: {book.subtitle}</span>}
          </button>
        </h3>
        <Byline book={book} />
        {series && <p className={styles.series}>{series}</p>}
        {facts.length > 0 && <p className={styles.facts}>{facts.join(" · ")}</p>}
        <Markers book={book} accounts={context.accounts} hideLibrary={held} />
      </div>
      <div className={styles.side}>
        <PriceBlock book={book} />
        {action ?? <WishlistButton book={book} context={context} />}
      </div>
    </article>
  );
}

export function Byline({ book }: { book: CatalogBook }) {
  if (!book.authors.length && !book.narrators.length) return null;
  return (
    <p className={styles.byline}>
      {book.authors.length > 0 && (
        <>
          By <span className={styles.strong}>{book.authors.map((author) => author.name).join(", ")}</span>
        </>
      )}
      {book.authors.length > 0 && book.narrators.length > 0 && " · "}
      {book.narrators.length > 0 && <>Narrated by {book.narrators.join(", ")}</>}
    </p>
  );
}

export function PriceBlock({ book, large = false }: { book: CatalogBook; large?: boolean }) {
  const price = priceParts(book.price);
  if (!price) return null;
  return (
    <div className={`${styles.price} ${large ? styles.priceLarge : ""}`}>
      <span>
        {price.was && (
          <s className={styles.was}>
            <span className="visually-hidden">List price </span>
            {price.was}
          </s>
        )}{" "}
        <span className={styles.member}>
          {price.was && <span className="visually-hidden">Member price </span>}
          {price.main}
        </span>
      </span>
      {price.credits && <span className={styles.credits}>{price.credits}</span>}
    </div>
  );
}

/** In library, then each wishlist the book is on, then Plus: always in this order. */
export function Markers({
  book,
  accounts,
  hideLibrary = false,
}: {
  book: CatalogBook;
  accounts: Account[];
  hideLibrary?: boolean;
}) {
  const named = (id: number) => accounts.find((account) => account.id === id);
  const show = (!hideLibrary && book.in_library) || book.on_wishlist.length > 0 || book.plus;
  if (!show) return null;
  return (
    <ul className={styles.markers} aria-label="Markers">
      {!hideLibrary && book.in_library && (
        <li>
          <LibraryMarker book={book} accounts={accounts} />
        </li>
      )}
      {book.on_wishlist.map((id) => (
        <li key={id} className={`${styles.marker} ${styles.wished}`}>
          <Icon name="heart" size={12} filled />
          {accounts.length > 1 ? `On ${wishlistName(named(id), accounts.length)}` : "On wishlist"}
        </li>
      ))}
      {book.plus && (
        <li className={`${styles.marker} ${styles.plus}`} title="Included in Audible Plus">
          Plus
        </li>
      )}
    </ul>
  );
}

function LibraryMarker({ book, accounts, large = false }: { book: CatalogBook; accounts: Account[]; large?: boolean }) {
  if (book.library_book_id == null) return null;
  const first = book.library[0];
  const account = accounts.find((one) => one.id === first?.account_id);
  const label = large ? `In ${libraryName(account, accounts.length)}` : "In library";
  return (
    <Link
      to={`/library/${book.library_book_id}`}
      className={`${styles.marker} ${styles.inLibrary} ${large ? styles.markerLarge : ""}`}
    >
      <Icon name="check" size={12} strokeWidth={2.8} />
      {label} →
    </Link>
  );
}

/**
 * Add to wishlist, or Remove from wishlist: straight away for one account, from a menu
 * when several could take it. No button for a book every account it could go to owns.
 */
export function WishlistButton({
  book,
  context,
  small = false,
}: {
  book: CatalogBook;
  context: CatalogContext;
  small?: boolean;
}) {
  const { add, remove } = useWishlistChange();
  const toast = useToast();
  const choices = wishlistChoices(book, context.accounts, context.countryCode);
  const total = context.accounts.length;
  const name = (account: Account) => wishlistName(account, total);

  const run = (kind: "add" | "remove", account: Account) => {
    const mutation = kind === "add" ? add : remove;
    mutation.mutate(
      { accountId: account.id, asin: book.asin },
      {
        onSuccess: () => toast.success(`${kind === "add" ? "Added to" : "Removed from"} ${name(account)}`),
        onError: (error) =>
          toast.error(
            `Could not ${kind === "add" ? "add" : "remove"} ${book.title} ${kind === "add" ? "to" : "from"} the wishlist`,
            error,
          ),
      },
    );
  };

  const busy = add.isPending ? "Adding…" : remove.isPending ? "Removing…" : null;
  const className = `btn btn-secondary ${small ? "btn-small" : ""} ${styles.wishButton}`;
  if (busy) {
    return (
      <button type="button" className={className} disabled aria-busy="true">
        <Icon name="spinner" size={15} className={styles.spin} />
        {busy}
      </button>
    );
  }

  if (choices.remove.length > 0) {
    const label = "Remove from wishlist";
    const icon = <Icon name="heart" size={15} filled className={styles.heartOn} />;
    return choices.remove.length === 1 ? (
      <button type="button" className={className} onClick={() => run("remove", choices.remove[0] as Account)}>
        {icon}
        {label}
      </button>
    ) : (
      <AccountMenu
        label={label}
        icon={icon}
        heading="Remove from the wishlist of"
        accounts={choices.remove}
        className={className}
        onPick={(account) => run("remove", account)}
      />
    );
  }

  if (choices.add.length > 0) {
    const label = "Add to wishlist";
    const icon = <Icon name="heart" size={15} />;
    const sorted = [...choices.add].sort(
      (a, b) => Number(b.id === context.searchedId) - Number(a.id === context.searchedId),
    );
    return choices.add.length === 1 ? (
      <button type="button" className={className} onClick={() => run("add", choices.add[0] as Account)}>
        {icon}
        {label}
      </button>
    ) : (
      <AccountMenu
        label={label}
        icon={icon}
        heading="Add to the wishlist of"
        accounts={sorted}
        searchedId={context.searchedId}
        className={className}
        foot="Only accounts on this marketplace can take this book."
        onPick={(account) => run("add", account)}
      />
    );
  }
  return null;
}

function AccountMenu({
  label,
  icon,
  heading,
  accounts,
  searchedId,
  className,
  foot,
  onPick,
}: {
  label: string;
  icon: ReactNode;
  heading: string;
  accounts: Account[];
  searchedId?: number;
  className: string;
  foot?: string;
  onPick: (account: Account) => void;
}) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const menuId = useId();

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
    <div ref={wrap} className={styles.menuWrap}>
      <button
        type="button"
        className={className}
        aria-haspopup="true"
        aria-expanded={open}
        aria-controls={menuId}
        onClick={() => setOpen(!open)}
      >
        {icon}
        {label}
        <Icon name="chevronDown" size={14} />
      </button>
      {open && (
        <div id={menuId} className={styles.menu}>
          <span className={styles.menuHeading}>{heading}</span>
          {accounts.map((account) => (
            <button
              key={account.id}
              type="button"
              className={styles.menuItem}
              onClick={() => {
                setOpen(false);
                onPick(account);
              }}
            >
              {account.name}
              {account.id === searchedId && <span className={styles.menuNote}>searched</span>}
            </button>
          ))}
          {foot && <span className={styles.menuFoot}>{foot}</span>}
        </div>
      )}
    </div>
  );
}
