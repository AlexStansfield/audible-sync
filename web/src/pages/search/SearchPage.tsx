/**
 * Search (Search.dc.html): the Audible catalogue on the marketplace of the account
 * searched through, with each book's markers and Add to wishlist. A `series:` in the
 * box shows the series instead, its books by position (DESIGN.md, Series).
 */
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { ApiError } from "../../api/client";
import { useStatus } from "../../api/queries";
import type { components } from "../../api/schema";
import { BookPanel } from "../../catalog/BookPanel";
import { BookRow, type CatalogContext, CatalogCover, Markers, WishlistButton } from "../../catalog/BookRow";
import {
  type CatalogSort,
  hasTerms,
  reach,
  SEARCH_PAGE_SIZE,
  useCatalogSearch,
  useCategories,
  useMarketplaces,
  useSeries,
  useSeriesMatches,
} from "../../catalog/queries";
import { byPosition, type CatalogBook, editionDifference, shownEdition } from "../../catalog/text";
import { ErrorCard } from "../../components/ErrorCard";
import { Icon } from "../../components/Icon";
import { Pager } from "../../components/Pager";
import { PrefixSearchBox, SearchChips } from "../../components/PrefixSearch";
import { Skeleton } from "../../components/Skeleton";
import { plural } from "../../lib/format";
import { describeSearch, type Parsed, parseSearch } from "../../lib/prefixSearch";
import styles from "./Search.module.css";
import { DEFAULT_QUERY, MAX_SEARCH, parse, rangeText, type SearchQuery, SORTS, serialise } from "./searchQuery";

type SeriesBook = components["schemas"]["SeriesBookOut"];
type Category = components["schemas"]["CategoryOut"];

const EXAMPLES = ["author:Adrian Tchaikovsky", "series:The Expanse", "dragons narrator:Ray Porter"];

export function SearchPage() {
  const [params, setParams] = useSearchParams();
  const query = parse(params);
  const status = useStatus();
  const marketplaces = useMarketplaces();

  const update = (changes: Partial<SearchQuery>, options: { replace?: boolean } = {}) => {
    setParams(serialise({ ...query, page: 1, series: null, book: null, ...changes }), { replace: options.replace });
  };

  const accounts = status.data?.accounts ?? [];
  const signedIn = accounts.filter((account) => !account.needs_login);
  const searched = signedIn.find((account) => account.id === query.accountId) ?? signedIn[0];
  const domain = siteOf(marketplaces.data, searched?.country_code);
  const parsed = parseSearch(query.q);
  const categoryId = query.subcategory ?? query.category;
  const seriesMode = Boolean(parsed.series);
  const terms = hasTerms(parsed, categoryId);
  const ready = status.data !== undefined && signedIn.length > 0;
  // The pager never offers a page past Audible's reach; a bookmark asking for one gets the last
  const lastReachable = Math.ceil(reach(query.sort) / SEARCH_PAGE_SIZE);
  const page = Math.min(query.page, lastReachable);

  const search = useCatalogSearch(
    { accountId: searched?.id ?? null, parsed, categoryId, sort: query.sort, page },
    ready && terms && !seriesMode,
  );
  const categories = useCategories(searched?.id ?? null, ready);

  // A page within Audible's reach but past this search's results: back to the first
  const pastEnd = search.error instanceof ApiError && search.error.status === 422 && page > 1;
  useEffect(() => {
    if (pastEnd) setParams(serialise({ ...query, page: 1 }), { replace: true });
  });

  const context: CatalogContext = {
    accounts,
    countryCode: searched?.country_code,
    searchedId: searched?.id,
    onOpen: (asin) => setParams(serialise({ ...query, book: asin })),
  };
  const refused = [search.error, categories.error].some((error) => error instanceof ApiError && error.status === 409);
  const noAccount = (status.data !== undefined && signedIn.length === 0) || refused;

  return (
    <>
      <header className="page-header">
        <div className={styles.titleBlock}>
          <h1 className="page-title">Search</h1>
          <span className="page-subtitle">
            {searched && domain
              ? `The Audible catalogue on ${domain}, with ${signedIn.length > 1 ? `${searched.name}'s` : "your"} prices`
              : "The Audible catalogue"}
          </span>
        </div>
      </header>

      {noAccount ? (
        <NoAccount />
      ) : (
        <>
          <section aria-label="Search" className={styles.filters}>
            <div className={styles.toolbar}>
              <PrefixSearchBox
                id="search-q"
                label="Search the catalogue"
                value={query.q}
                onSearch={(q) => update({ q }, { replace: true })}
                placeholder="Title, author or narrator, or author: narrator: series: title:"
                maxLength={MAX_SEARCH}
              />
              {signedIn.length > 1 && (
                <label className={styles.selectLabel}>
                  Search in
                  <select
                    className={styles.select}
                    value={searched?.id ?? ""}
                    onChange={(event) => update({ accountId: Number(event.target.value) })}
                  >
                    {signedIn.map((account) => (
                      <option key={account.id} value={account.id}>
                        {account.name}
                        {marketplaceSuffix(marketplaces.data, account.country_code)}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <CategorySelects
                categories={categories.data ?? []}
                query={query}
                disabled={seriesMode}
                onChange={(category, subcategory) => update({ category, subcategory })}
              />
              <label className={styles.selectLabel}>
                Sort
                <select
                  className={styles.select}
                  value={query.sort}
                  disabled={seriesMode}
                  onChange={(event) => update({ sort: event.target.value as CatalogSort })}
                >
                  {SORTS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            {seriesMode && (
              <p className="hint">A series is shown in reading order, so Category and Sort do not apply.</p>
            )}
            <SearchChips text={query.q} onChange={(q) => update({ q })} />
          </section>

          {!terms && !seriesMode ? (
            <Intro onExample={(q) => update({ q })} />
          ) : seriesMode ? (
            <SeriesSection
              parsed={parsed}
              query={query}
              accountId={searched?.id ?? null}
              domain={domain}
              context={context}
              onChoose={(asin) => setParams(serialise({ ...query, series: asin, book: null }))}
              onBack={() => setParams(serialise({ ...query, series: null, book: null }))}
              onClear={() => update({ q: "" })}
            />
          ) : search.isError && !search.data ? (
            pastEnd ? null : (
              <ErrorCard
                title="Could not search the catalogue"
                error={search.error}
                onRetry={() => void search.refetch()}
              />
            )
          ) : !search.data ? (
            <ResultsSkeleton />
          ) : search.data.items.length === 0 && search.data.total === 0 ? (
            <NoResults
              text={`Nothing on ${domain ?? "Audible"} matches ${describeSearch(query.q) || "this search"}${categoryText(categories.data, query)}.`}
              onClear={() => setParams(serialise({ ...DEFAULT_QUERY, accountId: query.accountId }))}
            />
          ) : (
            <Results
              data={search.data}
              page={page}
              domain={domain}
              context={context}
              onPage={(next) => setParams(serialise({ ...query, page: next }))}
            />
          )}
        </>
      )}

      {query.book && (
        <BookPanel
          asin={query.book}
          shown={search.data?.items.find((book) => book.asin === query.book)}
          accountId={searched?.id ?? null}
          marketplace={domain}
          context={context}
          onClose={() => setParams(serialise({ ...query, book: null }))}
        />
      )}
    </>
  );
}

/** The marketplace's site, `audible.co.uk`: the service gives the domain without `audible.`. */
function siteOf(markets: components["schemas"]["MarketplaceOut"][] | undefined, code: string | undefined) {
  const domain = markets?.find((market) => market.country_code === code)?.domain;
  return domain ? `audible.${domain}` : undefined;
}

function marketplaceSuffix(markets: components["schemas"]["MarketplaceOut"][] | undefined, code: string): string {
  const site = siteOf(markets, code);
  return site ? ` · ${site}` : "";
}

/** " in Science Fiction & Fantasy › Military", for the no-results line. */
function categoryText(categories: Category[] | undefined, query: SearchQuery): string {
  const parent = categories?.find((category) => category.id === query.category);
  if (!parent) return "";
  const child = parent.children.find((category) => category.id === query.subcategory);
  return ` in ${parent.name}${child ? ` › ${child.name}` : ""}`;
}

function CategorySelects({
  categories,
  query,
  disabled,
  onChange,
}: {
  categories: Category[];
  query: SearchQuery;
  disabled: boolean;
  onChange: (category: string | null, subcategory: string | null) => void;
}) {
  const parent = categories.find((category) => category.id === query.category);
  return (
    <>
      <label className={styles.selectLabel}>
        Category
        <select
          className={styles.select}
          value={query.category ?? ""}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value || null, null)}
        >
          <option value="">All categories</option>
          {categories.map((category) => (
            <option key={category.id} value={category.id}>
              {category.name}
            </option>
          ))}
        </select>
      </label>
      {parent && parent.children.length > 0 && (
        <label className={styles.selectLabel}>
          <span className="visually-hidden">Within {parent.name}</span>
          <select
            className={styles.select}
            value={query.subcategory ?? ""}
            disabled={disabled}
            onChange={(event) => onChange(parent.id, event.target.value || null)}
          >
            <option value="">All of {parent.name}</option>
            {parent.children.map((category) => (
              <option key={category.id} value={category.id}>
                {category.name}
              </option>
            ))}
          </select>
        </label>
      )}
    </>
  );
}

function Results({
  data,
  page,
  domain,
  context,
  onPage,
}: {
  data: components["schemas"]["CatalogSearchOut"];
  page: number;
  domain: string | undefined;
  context: CatalogContext;
  onPage: (page: number) => void;
}) {
  const reachable = Math.min(data.total, data.reachable);
  const pages = Math.max(1, Math.ceil(reachable / SEARCH_PAGE_SIZE));
  const capped = data.total > data.reachable;
  return (
    <section aria-label="Results" className={styles.results}>
      <p className={styles.count}>
        {plural(data.total, "result")} on {domain ?? "Audible"}
        {capped && (
          <span className={styles.muted}>
            {" "}
            · Audible shows only the first {data.reachable.toLocaleString()}; narrow the search to see the rest
          </span>
        )}
      </p>
      <div className={styles.card}>
        {data.items.map((book) => (
          <BookRow key={book.asin} book={book} context={context} />
        ))}
      </div>
      <Pager page={page} pages={pages} label={rangeText(page, SEARCH_PAGE_SIZE, reachable)} onPage={onPage} />
      {capped && page === pages && (
        <p className={`banner-warn ${styles.cappedNote}`}>
          <Icon name="warning" size={18} />
          <span>
            Showing the first {data.reachable.toLocaleString()} of {data.total.toLocaleString()} results. Audible pages
            no further. Narrow the search with a prefix or a category to see more.
          </span>
        </p>
      )}
    </section>
  );
}

function ResultsSkeleton() {
  return (
    <div className={styles.card} role="status" aria-busy="true" aria-label="Searching">
      {Array.from({ length: 5 }, (_, index) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: identical placeholders
        <div key={index} className={styles.skeletonRow}>
          <Skeleton width={88} height={88} style={{ borderRadius: 6 }} />
          <span className={styles.skeletonLines}>
            <Skeleton width="55%" height={14} />
            <Skeleton width="40%" />
            <Skeleton width="30%" />
            <Skeleton width={90} height={18} style={{ borderRadius: 999 }} />
          </span>
          <span className={styles.skeletonSide}>
            <Skeleton width={60} height={18} />
            <Skeleton width={130} height={36} />
          </span>
        </div>
      ))}
    </div>
  );
}

function Intro({ onExample }: { onExample: (q: string) => void }) {
  return (
    <section aria-labelledby="intro-h" className={styles.intro}>
      <div className={styles.introText}>
        <h2 id="intro-h" className={styles.emptyTitle}>
          Search the Audible catalogue
        </h2>
        <p className={styles.emptyText}>
          This searches everything Audible sells on the marketplace, not your library. A book a library here already
          holds is marked In library, and the prices are the searched account's.
        </p>
        <div className={styles.examples}>
          {EXAMPLES.map((example) => (
            <button
              key={example}
              type="button"
              className="btn btn-secondary btn-small"
              onClick={() => onExample(example)}
            >
              <code>{example}</code>
            </button>
          ))}
        </div>
      </div>
      <dl className={styles.prefixes}>
        <div>
          <dt>author:</dt>
          <dd>books by an author</dd>
        </div>
        <div>
          <dt>narrator:</dt>
          <dd>books read by a narrator</dd>
        </div>
        <div>
          <dt>series:</dt>
          <dd>a series, in reading order</dd>
        </div>
        <div>
          <dt>title:</dt>
          <dd>words in the title only</dd>
        </div>
      </dl>
    </section>
  );
}

function NoResults({ text, onClear }: { text: string; onClear: () => void }) {
  return (
    <section aria-labelledby="nores-h" className={styles.emptyCard}>
      <span className={styles.emptyIcon}>
        <Icon name="searchEmpty" size={26} />
      </span>
      <h2 id="nores-h" className={styles.emptyTitle}>
        No books match this search
      </h2>
      <p className={styles.emptyText}>{text}</p>
      <button type="button" className="btn btn-secondary" onClick={onClear}>
        Clear filters
      </button>
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
        No account to search with
      </h2>
      <p className={styles.emptyText}>Add an Audible account, or sign in to one, to search the catalogue.</p>
      <Link to="/accounts" className="btn btn-primary">
        Go to Accounts
      </Link>
    </section>
  );
}

// --- series --------------------------------------------------------------------

/** The other parts of the box narrow a series' books, here rather than at Audible. */
function narrows(book: CatalogBook, parsed: Parsed): boolean {
  const has = (haystack: string, needle: string | undefined) =>
    !needle || haystack.toLowerCase().includes(needle.toLowerCase());
  return (
    has(`${book.title} ${book.subtitle}`, parsed.keywords || undefined) &&
    has(`${book.title} ${book.subtitle}`, parsed.title) &&
    has(book.narrators.join(" "), parsed.narrator)
  );
}

function SeriesSection({
  parsed,
  query,
  accountId,
  domain,
  context,
  onChoose,
  onBack,
  onClear,
}: {
  parsed: Parsed;
  query: SearchQuery;
  accountId: number | null;
  domain: string | undefined;
  context: CatalogContext;
  onChoose: (asin: string) => void;
  onBack: () => void;
  onClear: () => void;
}) {
  const matches = useSeriesMatches(parsed.series, parsed.author, accountId);
  const only = matches.data?.length === 1 ? matches.data[0]?.asin : undefined;
  const chosen = query.series ?? only ?? null;
  const series = useSeries(chosen, accountId);

  if (matches.isError) {
    return (
      <ErrorCard title="Could not look for the series" error={matches.error} onRetry={() => void matches.refetch()} />
    );
  }
  if (!matches.data) return <ResultsSkeleton />;
  if (matches.data.length === 0) {
    return (
      <NoResults text={`No series called “${parsed.series}” was found on ${domain ?? "Audible"}.`} onClear={onClear} />
    );
  }
  if (!chosen) {
    return (
      <section aria-labelledby="choose-h" className={styles.card}>
        <div className={styles.cardHead}>
          <h2 id="choose-h" className="card-title">
            {plural(matches.data.length, "series", "series")} match “{parsed.series}”
          </h2>
          <p className="hint">
            The counts are a rough guide: they are the books a search for the name found, not the size of the series.
          </p>
        </div>
        <ul className={styles.matches}>
          {matches.data.map((match) => (
            <li key={match.asin}>
              <button type="button" className={styles.match} onClick={() => onChoose(match.asin)}>
                <span className={styles.matchTitle}>{match.title}</span>
                {match.exact && <span className={styles.exact}>Exact match</span>}
                <span className={styles.muted}>{plural(match.books_found, "book")} found</span>
                <Icon name="chevron" size={16} />
              </button>
            </li>
          ))}
        </ul>
      </section>
    );
  }
  if (series.isError) {
    return <ErrorCard title="Could not load the series" error={series.error} onRetry={() => void series.refetch()} />;
  }
  if (!series.data) return <ResultsSkeleton />;

  const books = series.data.books.filter((entry) => narrows(entry.book, parsed));
  const groups = byPosition(books);
  const held = books.filter((entry) => entry.book.in_library).length;
  const firstWithEditions = groups.find((group) => group.editions.length > 1);
  const others = matches.data.length - 1;
  return (
    <section aria-labelledby="series-h" className={styles.card}>
      <div className={styles.cardHead}>
        {query.series && others > 0 && (
          <button type="button" className={`link-btn ${styles.back}`} onClick={onBack}>
            ← {plural(others, "other series", "other series")} matched
          </button>
        )}
        <span className={styles.eyebrow}>Series</span>
        <h2 id="series-h" className={styles.seriesTitle}>
          {series.data.title}
        </h2>
        <p className={styles.muted}>
          {plural(books.length, "book")} in {plural(groups.length, "position")}, in reading order
          {held > 0 && ` · ${held} in a library here`}
        </p>
      </div>
      {groups.map((group) => (
        <Position
          key={group.sequence ?? "none"}
          sequence={group.sequence}
          editions={group.editions}
          context={context}
          startOpen={group === firstWithEditions}
        />
      ))}
    </section>
  );
}

function Position({
  sequence,
  editions,
  context,
  startOpen,
}: {
  sequence: string | null;
  editions: SeriesBook[];
  context: CatalogContext;
  startOpen: boolean;
}) {
  const [open, setOpen] = useState(startOpen);
  const shown = shownEdition(editions);
  if (!shown) return null;
  const rest = editions.filter((entry) => entry !== shown);
  const listId = `editions-${shown.book.asin}`;
  return (
    <div className={styles.position}>
      <span className={styles.sequence}>
        <span className="visually-hidden">{sequence ? "Book " : "No position"}</span>
        {sequence ?? <span aria-hidden="true">–</span>}
      </span>
      <div className={styles.positionBody}>
        <BookRow book={shown.book} context={context} coverSize={64} />
        {rest.length > 0 && (
          <>
            <button
              type="button"
              className={`btn btn-secondary btn-small ${styles.editionsToggle}`}
              aria-expanded={open}
              aria-controls={listId}
              onClick={() => setOpen(!open)}
            >
              {open
                ? rest.length === 1
                  ? "Hide the other edition"
                  : `Hide the other ${rest.length} editions`
                : `${editions.length} editions`}
              <Icon name="chevronDown" size={14} />
            </button>
            {open && (
              <ul id={listId} className={styles.editions}>
                {rest.map((entry) => (
                  <li key={entry.book.asin} className={styles.edition}>
                    <button
                      type="button"
                      className={styles.editionCover}
                      tabIndex={-1}
                      aria-hidden="true"
                      onClick={() => context.onOpen(entry.book.asin)}
                    >
                      <CatalogCover book={entry.book} size={40} />
                    </button>
                    <div className={styles.editionMain}>
                      <button
                        type="button"
                        className={styles.editionTitle}
                        onClick={() => context.onOpen(entry.book.asin)}
                      >
                        {entry.book.title}
                      </button>
                      <span className={styles.muted}>{editionDifference(entry.book)}</span>
                      <Markers book={entry.book} accounts={context.accounts} />
                    </div>
                    <WishlistButton book={entry.book} context={context} small />
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    </div>
  );
}
