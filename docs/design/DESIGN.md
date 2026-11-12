# Web app design (Milestone 4, and v3.4.0's Search and Wishlist)

> Where this document or a mockup in `screens/` disagrees with the code, the code wins: the mockups show the design as first planned, and are not kept up to date with every change.

The design hand-off for the web app on top of the API. It describes **what to build**: the screens, what each one reads and writes, how it behaves, and the backend work the UI depends on. The look is in [`screens/`](screens/).

## The screen files

`screens/*.dc.html` are the mockups, exported from the design canvas. Each file is one screen as self-contained HTML: inline styles carry the exact spacing, sizes and colours. They are a template format, not plain HTML, so read them as a spec rather than opening them in a browser:

- `{{name}}` is a value from the `renderVals()` method in the `<script type="text/x-dc">` block at the foot of the file. That block also holds the sample data (books, runs, log lines) and the conditions behind each state.
- `<sc-for list="{{items}}" as="item">` repeats its content once per item; `<sc-if value="{{cond}}">` shows its content only when the condition holds.
- `data-props` on the script tag lists the screen's variants (for example `status` on Book detail, `step` on Accounts, `theme` everywhere). Each value is a state the real screen must support.
- `canvas.json` is the canvas layout; ignore it.

The sample data (titles, counts, the ASIN, run numbers) is illustrative. The real values come from the API.

| File | Screen | Route |
|------|--------|-------|
| `Login.dc.html` | Sign in | `/login` |
| `Main.dc.html` | Dashboard | `/` |
| `Library.dc.html` | Library, grid and table views | `/library` |
| `Book.dc.html` | Book detail | `/library/:id` |
| `Accounts.dc.html` | Accounts and the Audible sign-in dialog | `/accounts` |
| `Activity.dc.html` | Run history and the service log | `/activity` |
| `Settings.dc.html` | Settings | `/settings` |
| `Search.dc.html` | Search the Audible catalogue, a book's panel, a series (v3.4.0) | `/search` |
| `Wishlist.dc.html` | Every account's wishlist (v3.4.0) | `/wishlist` |
| `Profile.dc.html` | Your sign-in (change username and password) | `/profile` |
| `NotFound.dc.html` | 404 for an unknown route or a book that does not exist (`kind`) | any unknown path |
| `Unreachable.dc.html` | The service cannot be reached at all | - |
| `Patterns.dc.html` | Common patterns: skeletons, a failed request, toasts | - |
| `State*.dc.html`, `Phone*.dc.html`, `Light*.dc.html` | Frames that show a screen above with one variant switched on (first run, empty library, bulk selection, each Accounts dialog, phone width, light theme). They contain no design of their own: read the screen they import and its variant | - |

Every screen's `data-props` lists its variants. Round 2 added: Dashboard `firstRun`, `generatedPassword`; Library `empty` (`noBooks`, `noResults`) and `bulk` (`selecting`, `confirmDelete`, `result`); Book `status` = `downloading`, `monitored`, `sizeKnown`, `noRun`; Accounts `step` = `expired`, `failed`, `relogin`, `rename`, `remove`, `menu`; Sign in `forgot`; Settings `token` (`masked`, `confirm`, `regenerated`), `lowDisk`; Activity `expanded`. v3.4.0 added Search `state`, `series`, `action`, `tips`, `detail`, `capped`, `accounts`; Wishlist `state`, `remove`, `refreshing`, `partial`, `accounts`; Library `search` and `tips`.

## Stack

- **Vite + React + TypeScript**, a single-page app in `web/`. Client-side routing.
- **Built into the Docker image and served by FastAPI** at `/`, so there is one container, one port and no CORS in production. API routes stay under `/api`; every other path that is not a file serves `index.html` so client routes survive a reload.
- **Development:** the Vite dev server proxies `/api` to the service, or the service sets `AUDIBLE_SYNC_CORS_ORIGINS` to the dev server's origin.
- **Server state:** a query library (TanStack Query or similar) so one polled `GET /api/status` feeds every screen that shows the run in flight.
- **Types:** generate them from `/openapi.json` rather than writing them by hand; `src/audible_sync/api/schemas.py` is the contract.
- Keep dependencies small. No component library is required: the design is plain elements with the styles below.

## Look

A dark, dense admin UI in the style of Sonarr, Radarr and Audiobookshelf, with a light theme. A fixed sidebar on the left (it stacks above the content on a phone), content in cards on a plain background. Covers are square.

### Type

- **Inter** (400, 500, 600, 700) for everything; **JetBrains Mono** (400, 500, 700) for paths, templates, ASINs, codes, durations and the log.
- Body 14px / 1.45. Page titles 24px bold; card titles 15-16px semibold; labels 13px semibold; secondary text 12-13px.

### Theme values

One set of CSS variables, switched by `data-theme` on the root element. Use these names in the app.

| Variable | Dark | Light | Use |
|----------|------|-------|-----|
| `--bg` | `#0e1014` | `#f4f5f7` | Page and input background |
| `--nav` | `#12141a` | `#ffffff` | Sidebar |
| `--panel` | `#161920` | `#ffffff` | Cards |
| `--raised` | `#1d212a` | `#eef0f3` | Active nav item, secondary buttons, callouts |
| `--line` | `#272c36` | `#e2e5ea` | Dividers, card borders |
| `--line-strong` | `#3a404c` | `#c6ccd5` | Input and button borders, switches off |
| `--code` | `#0b0d10` | `#f4f5f7` | The log panel |
| `--text` | `#e7e9ee` | `#15181e` | Primary text |
| `--text-2` | `#b6bdca` | `#3b4250` | Nav links, secondary text |
| `--muted` | `#9ba3b4` | `#5b6372` | Hints, labels, metadata |
| `--faint` | `#6f7889` | `#8b929e` | Disabled only |
| `--accent` | `#f2a541` | `#f2a541` | Primary button fill, switches on, progress, focus |
| `--accent-text` | `#f2a541` | `#9a5300` | Links and accent-coloured text |
| `--accent-hover` | `#ffc477` | `#7a4100` | Link hover |
| `--ok` | `#4ccf8f` | `#14794a` | Downloaded, success |
| `--info` | `#6fb1ff` | `#1c5fbf` | Waiting, info log lines |
| `--bad` | `#ff7a7a` | `#c22f2f` | Failed, aborted, errors |
| `--bad-text` | `#ff9a9a` | `#b02424` | Error messages |
| `--violet` | `#b79cff` | `#6541c4` | Unavailable |
| `--bad-line` | `#6b3434` | `#efb4b4` | Danger buttons, error borders |
| `--warn-line` | `#6b5228` | `#eccf96` | Warning banner border |
| `--pill` | `rgba(14,16,20,0.82)` | `rgba(255,255,255,0.94)` | Status badge over a cover |

Fixed in both themes: text on an accent button `#1a1206`; the destructive confirm button `#e55b5b` with white text; the dialog backdrop `rgba(5,6,8,0.72)`. A status chip is the status colour as text on the same colour at 14% opacity. The light values are darker for a reason: every text colour must keep 4.5:1 contrast on its background, so check any new colour before adding it.

**Theme choice:** System (follows `prefers-color-scheme`, the default), Light or Dark, set under Settings › Appearance and stored in `localStorage`, not in the settings API, so each browser keeps its own.

### Shapes and spacing

Cards: 12px radius, 1px `--line` border, 18-24px padding. Inputs and buttons: 8px radius, 42-46px tall. Pills: fully rounded. Page padding 28px top, 36px sides. Gaps of 14-24px between cards. Every clickable target is at least 44px tall, or 32px for small inline actions.

### Statuses

Use the same colour and word for a status everywhere:

| Book status | Label | Colour |
|-------------|-------|--------|
| `downloaded` | Downloaded | `--ok` |
| `waiting_download` | Waiting | `--info` |
| `downloading` | Downloading | `--accent-text` |
| `unavailable` | Unavailable | `--violet` |
| `failed` | Failed | `--bad` |

Run outcomes: `success` → `--ok`; `partial` and `running` → `--accent-text`; `failed` and `aborted` → `--bad`; `cancelled` → `--text-2`. An unmonitored book is shown at reduced opacity with an outline bookmark.

## Sign-in to the web app

There is one user and no sign-up.

- **First start:** the backend creates a user named `admin` with a generated password and logs it once, the way it logs a generated API token now. The sign-in screen says so: "First start? Sign in as admin with the password shown in the service log."
- **Sign in:** username and password; "Keep me signed in" makes the session cookie persistent. A wrong username or password shows one message that says only that, without revealing which one was wrong.
- **Session:** an HTTP-only, `SameSite=Strict` cookie. The bearer token keeps working for scripts and the CLI. Any `401` from the API sends the app back to `/login`, then returns you to the page you were on.
- **Your sign-in** (`/profile`, reached from the username at the foot of the sidebar):
  - Change username: the new username plus the current password.
  - Change password: the current password, the new one twice. At least 12 characters, with a strength meter; a mismatch is shown under the confirm field. Changing the password signs out every other session and keeps this one.
  - While the generated password is still in use, a banner here (and, suggested, a dot on the sidebar username) asks you to choose your own.
- **Sign out** is at the foot of the sidebar.

## Layout shared by every screen

Sidebar: the logo and version (from `GET /api/health`); Dashboard, Library (with the total book count), Search, Wishlist (with the number of books on every wishlist), Activity, Accounts (with a red dot when an account needs a sign-in), Settings; at the foot, the service state (healthy, or "Syncing · book 3 of 10" while a run is in flight), the username linking to Your sign-in, and Sign out.

**Polling `GET /api/status`:** every 2 s while `scheduler.running` is true, every 10 s otherwise (30 s at first; a run the schedule started went unseen for up to half a minute), and once straight after any action that starts or stops a run. The dashboard's run card, the sidebar's service state and the library's Downloading badges all read this one query. When a run finishes, refetch the stats, the library and the runs.

## Screens

### Dashboard (`Main.dc.html`)

- **Header:** the number of accounts and the schedule; the next run time with a relative countdown (`scheduler.next_run_at`). **Sync now** (`POST /api/sync`) when idle; **Cancel run** (`POST /api/sync/cancel`) while running. A `409` means the state changed under you: refetch the status and show a brief message.
- **Run card**, only while a run is in flight (`current_run`): the cover and title of `current_run.book`, the stage (Syncing / Downloading / Cancelling), the run id, account and start time, a progress bar of `transfer.bytes / transfer.total` (indeterminate when `total` is null), and "Book `books_done` of `books_total` this run".
- **Totals** (`GET /api/stats`): In library, Downloaded (with bytes on disk), Waiting, Unavailable, Failed. Each tile opens the Library filtered to that status.
- **Accounts:** one row per account with its marketplace, totals, bytes on disk, last run, and a stacked bar of its books by status.
- **Recently downloaded:** six covers, newest first (`GET /api/books?status=downloaded&sort=downloaded_at`).
- **Needs attention:** shown only when there is something to show. Unavailable books Audible refused a licence for (`retryable_unavailable` from `GET /api/stats`), with **Retry unavailable** (`POST /api/books/retry-unavailable`, then a message with `requeued`); titles Audible withdrew need nothing doing and are not listed; failed books linking to the Library filtered to failed; an account that needs a sign-in, linking to Accounts.
- **Recent runs:** the last five (`GET /api/sync/runs?limit=5`), each linking to Activity.

### Library (`Library.dc.html`)

`GET /api/books` with `q`, `account_id`, `status`, `monitored`, `sort`, `order`, `page` and `page_size`. Keep every filter in the URL query string so a filtered view can be bookmarked and survives a reload.

- **Toolbar:** search (debounced by about 300 ms, over title, author and series; v3.4.0 adds the prefixes, see [Library: the search box](#library-the-search-box-search-tips)), Account, Sort (date added, title, author, release date, downloaded), and a grid/table view switch stored in `localStorage`.
- **Status filter chips** with counts from the stats (All, Downloaded, Waiting, Downloading, Unavailable, Failed), and an "Unmonitored only" checkbox (`monitored=false`).
- **Grid:** square covers (`GET /api/books/{id}/cover`, lazy loaded) with a status badge, a progress strip on the book downloading, and title and author under it. The bookmark button toggles monitoring (`PATCH /api/books/{id}` with `{"monitored": bool}`). Update it optimistically and roll back on an error.
- **Table:** title with a small cover, author, series, account, status, added, and a monitored switch. It scrolls sideways on narrow screens.
- **Pagination:** "1-24 of 354", previous and next, page numbers.
- **Retry unavailable** in the header, with the count it would queue (`retryable_unavailable`), shown only when that is not zero. The status chips' counts follow Monitored/Unmonitored only (`monitored_by_status`).
- **Empty states:** no books at all ("Add an Audible account to fetch your library", linking to Accounts); no results for the filters (with a clear-filters button).

### Book detail (`Book.dc.html`)

`GET /api/books/{id}`. Variants: `status` = downloaded, failed, unavailable or waiting.

- Large cover, the status chip, the account, the title and subtitle, authors, narrators.
- **Details:** series from `primary_series` ("Impossible Times #1"; never `series[0]`), length (`length` minutes as "7h 6m"), released, added, ASIN, genres, companion PDF yes/no, listening progress (`percent_complete`; "Finished" when `is_finished`).
- **Monitored** switch (`PATCH`).
- **Actions, by status:**
  - downloaded: **Redownload** (`POST /redownload`) and **Delete files** (`POST /delete-files`, behind a confirmation dialog that says the files go, the book is unmonitored and stays in the library)
  - failed or unavailable: **Retry download** (`POST /retry`)
  - always: **Refresh from Audible** (`POST /refresh`; a `502` means Audible did not answer, a `409` means the account needs a sign-in)
  - a `409` for a book a run is downloading: say so and offer to cancel the run
- **Download panel:** attempts (`attempts` of the `max_attempts` setting), last attempt, downloaded at, format. For a failed or unavailable book, `last_error` in a red box: "Gave up after 3 attempts" or "Audible refused the licence".
- **Files on disk:** the audio, cover, annotations and PDF paths, or "Not downloaded".

### Accounts (`Accounts.dc.html`)

`GET /api/accounts`. Variants: `step` = closed, setup, code, browser, done.

- **Account cards:** marketplace code, name, a state chip (Active, Paused, Needs sign-in), customer name, domain and date added, book count, last synced. Actions: **Sign in** (when `needs_login`) or **Sign in again**, a Sync switch (`PATCH` `enabled`), and a menu with Rename (`PATCH` `name`) and Remove.
- **Remove:** a confirmation dialog with a "Also remove this device from my Amazon account" checkbox (`DELETE /api/accounts/{id}?deregister=true`). It says the library rows and run history go and the files stay. A `409` means the run is on that account right now.
- **Help cards:** why "License not granted" means Sign in again, and what removing an account does.
- **Add account dialog,** three steps with a progress bar:
  1. **Setup:** Marketplace (`GET /api/marketplaces`), an optional name, and "Download the books already in this library" (`monitor_existing`), then **Get sign-in code** (`POST /api/accounts/login` with `method: "code"`).
  2. **Code:** the five-letter `code` in large mono boxes, a link to `verification_url`, Copy code, and a live countdown to `expires_at`. Poll `GET /api/accounts/login/{login_id}` every 3 s: `confirmed` goes to step 3, `expired` offers a new code, `failed` shows `error`. Signing in with a login an account already holds gives that account new credentials (`replaced`), and the done screen says it is signed in again rather than connected. A link switches to the browser sign-in for older accounts, cancelling the code first (`DELETE /api/accounts/login/{login_id}`); closing the dialog cancels a login that is still open the same way, and a toast says when the cancel found the code already confirmed.
  3. **Browser fallback:** open `url` in a new tab, then paste the "page not found" address (`POST /api/accounts/login/{login_id}` with `response_url`). A `400` means paste again; a `404` means start again.
  4. **Connected:** the account name, and **Done** or **Sync now**.
- **Sign in again** opens the same dialog with the marketplace fixed and `account_id` set (with `deregister_previous: true`), and the confirmation says the library is kept and, only when `requeued` says some were, how many refused books are waiting to download again.

### Activity (`Activity.dc.html`)

- **Run history:** `GET /api/sync/runs` with `limit`, `offset` and `account_id`: run id, outcome chip, account, started, duration (`finished_at - started_at`, or live for a running row), books seen, added, downloaded and failed, and the note (`error`, or the book in hand for the running row). "Load more" pages by `offset`.
- **Service log:** `GET /api/logs?limit=&level=`, with a minimum-level control (Debug, Info, Warning, Error) and Follow (refetch every 2 s and keep scrolled to the bottom; pause when the user scrolls up). Monospace, one line per record: time, level in its colour, logger, message; tracebacks wrap.

### Settings (`Settings.dc.html`)

`GET /api/settings`; save with `PUT /api/settings` sending **only the fields that changed**. The header shows "N unsaved changes" with Discard and Save. A `422` carries the validator's message: show it under the field it names. Variants: `format` = m4b or oga.

| Section | Fields |
|---------|--------|
| Appearance | Theme: System, Light, Dark (browser only, applies at once) |
| Schedule | `sync_enabled` switch; `sync_interval_minutes` as a number plus hours/minutes (at least 5 minutes), with the next run time; `auto_monitor_new` switch |
| Downloads | `max_download` (empty = no limit, sent as `null`), `max_attempts` (at least 1), `max_consecutive_failures` (empty = never, sent as `null`) |
| Audio format | `encoding_format` as two cards, M4B (original) and Ogg Opus; `bitrate` slider (1-256 kbps; the hint says 64 matches Audible) only for Ogg Opus |
| Folders and naming | `audiobook_folder`, `download_folder`, `folder_template`, `filename_template`; buttons that insert a placeholder at the cursor; a live preview of two sample paths (one series book, one not) worked out in the browser with the same rules as `src/audible_sync/pipeline/naming.py` |
| Notifications | `webhook_url` and **Send test** (`POST /api/settings/webhook/test` with the URL in the field); show the result inline |
| Advanced | `debug` switch (applies at once) |

The audio format, folder and naming sections say that changes apply to books downloaded from now on, and that existing files are not converted or renamed.

## Round 2: states, dialogs and new pages

Each item below is a variant on the screen named, so the same screen code renders it. The `State*` frames on the canvas show each one switched on.

### Dashboard: first run (`firstRun`)

What a new install shows after the first sign-in as `admin`: no accounts, nothing has run.

- The subtitle reads "No accounts yet"; Sync now and the next-run time are hidden.
- The generated-password banner sits at the top (it also shows on a normal dashboard while `generated_password` is true, the `generatedPassword` variant), linking to Your sign-in.
- A welcome card: "Welcome to Audible Sync", one paragraph, the primary button **Add an Audible account** (to Accounts), and three numbered steps: add an account, choose what to download, let it run.
- The five totals stay, at zero and dimmed. The accounts, recently downloaded, up next, needs-attention and recent-runs cards are hidden.
- Show this whenever `GET /api/accounts` is empty, whatever has run before.

### Dashboard: Up next

A card at the top of the right column: the books the next run will download, in order. That means waiting and monitored, oldest purchase first, for each enabled account. Each row shows its position, a small cover, the title, the author with the purchase date, and the account. Below the first four, a "+N more waiting" link opens the Library filtered to Waiting. The header gives the run time and the limit: "up to `max_download` books per account", or "everything waiting" when `max_download` is empty.

**API:** `GET /api/books?status=waiting_download&monitored=true&sort=date_added&order=asc&page_size=4`, per account or merged, works today. A dedicated `GET /api/queue` that applies the per-account limit would be more accurate.

### Library: empty states (`empty`)

- `noBooks`: the filters, the Select button and the pager are hidden; a dashed card reads "Your library is empty / Add an Audible account to fetch your library", with **Add an Audible account**.
- `noResults`: the filters stay as set; a card reads "No books match these filters", restates the search and status, and offers **Clear filters**. The pager is hidden.

### Library: bulk actions (`bulk`)

Built for an account added with "Download the books already in this library" turned off, whose whole back catalogue arrives unmonitored.

- **Select** in the header toggles selection mode (it becomes **Done**). In the grid, each cover gets a checkbox top right and a selected cover gets a 3px accent outline. In the table, a checkbox column appears, with a select-page checkbox in its header, and selected rows are tinted.
- **The action bar** sits above the books, with an accent border. It shows "N selected", **Select all on this page (24)**, **Select all 354 matching these filters** (which turns the label into "All 354 matching books selected"), and **Clear**. The actions are **Monitor**, **Unmonitor**, **Retry** and **Delete files**, the last in the destructive outline style.
- Only Delete files confirms, in a dialog: "Delete the files for N books?", saying what goes, that the books are unmonitored and stay in the library, and that books with no files or that a run is downloading are skipped. The confirm button reads "Delete files for N books".
- **After any action** a dismissible result banner reads, for example, "6 books monitored, 4 of them queued for download. 1 skipped because a run is downloading it." Monitor queues only the selected books that are waiting; failed and unavailable books keep their status until retried, so the banner says how many were queued rather than implying all of them were. The selection is cleared.
- Selection survives paging. "Select all matching" sends the filters, not the ids.

**Backend work needed:** `POST /api/books/bulk` with `{"action": "monitor"|"unmonitor"|"retry"|"delete_files", "ids": [...]}` or `{"action": ..., "filter": {account_id, status, monitored, q}}`. It returns `{"updated": n, "skipped": [{"id", "reason"}]}`. Skips cover books a run holds (`downloading`), retrying a downloaded book, and deleting files from a book with none.

### Book detail: downloading and unmonitored

- `status=downloading`: an accent-bordered panel above the actions reads "Downloading in run #148", with the book's place in the run, a progress bar of `transfer.bytes / transfer.total` from `GET /api/status` (`sizeKnown=false`: an indeterminate bar and "243 MB so far · size unknown"), and **Cancel run**. Redownload, Delete files and Refresh are disabled, with a line saying why. The Monitored switch stays usable.
- `status=downloading`, `noRun`: the row says `downloading` but no run is working on it (`GET /api/status` has no `current_run`, or its `book` is another one). This happens after a run was killed part way, or while a command-line run goes beside the service, and the claim is picked up again once it is six hours old (`STALE_CLAIM_SECONDS`). Show a warning-bordered note instead of the progress panel: "Marked as downloading since <last_attempt_at>; no run is working on it. It is picked up again automatically." No progress bar and no Cancel run. The actions stay disabled: they are unavailable while the book is marked downloading. The API refuses Delete files, Redownload and Retry (`409`) on such a row; it would accept Refresh, which is disabled anyway so every action behaves the same.
- `monitored=false`: the cover is shown at half opacity, a "Not monitored" outline chip with the outline bookmark sits beside the status, and a note says the book will not be downloaded and how to queue it.

### Accounts: dialogs (`step`)

- **Account menu** (`menu`): the ⋯ button opens a menu with Rename and Remove account (the latter in the error colour).
- **Rename** (`rename`): one Name field, prefilled, with "Only used in this app". Cancel and Save. `PATCH /api/accounts/{id}` `{"name"}`.
- **Remove** (`remove`): "Remove Alex (US)?", what is deleted (library rows, run history), what stays (files on disk, with the count), and a checked **Also remove this device from my Amazon account** (`deregister=true`). Keep account, and the destructive **Remove account**. A `409` means the run is on that account: say so.
- **Sign in again** (`relogin`, from Sign in or Sign in again on a card): the add-account dialog titled "Sign in again: Alex (UK)", with the marketplace fixed and shown locked, a note that the library and history are kept and refused books are queued again, and a checked "Remove the old device from my Amazon account" (`deregister_previous`). Then the same code step, with the account's own domain, and a done screen that says it is signed in again. `POST /api/accounts/login` with `account_id`.
- **Code expired** (`expired`): the code is shown greyed and struck through, with "This code has expired. A code lasts ten minutes." and **Get a new code** (a new `POST /api/accounts/login`).
- **Sign-in failed** (`failed`): the code greyed, an error box "Sign-in failed" with the login's `error` text as sent by the server, **Try again** and **Use browser sign-in**.

### Sign in: forgot password (`forgot`)

"Forgot your password?" sits beside the Password label. It swaps the form for a card: "Reset your password", an explanation that there is no email so the reset is done on the server, a code block with the command - `docker compose exec audible-sync uv run --no-sync audible-sync reset-password` (`audible-sync reset-password` outside Docker; `--username` also puts the name back to `admin`) - what happens next (sign in, choose your own password; every browser is signed out), and **Back to sign in**.

### Settings: API access and System

- **API access** (`token`): the token is shown masked to its last four characters, with **Copy** disabled and **Regenerate** in the destructive outline style. Regenerate confirms ("The current token stops working at once…", **Keep the current token** or **Regenerate**). Afterwards (`regenerated`) the full token is shown with an accent border, Copy is enabled, and a line says to copy it now because it is masked again after leaving the page. If the token comes from `AUDIBLE_SYNC_API_TOKEN`, say to change it there instead and disable Regenerate.
- **System:** the audiobooks folder with free and total space and a meter (`lowDisk`: the meter, text and border in the error colour, with a warning that downloads will fail when the disk is full), the app and FFmpeg versions (and whether libopus is available), where the encryption key comes from (environment variable, or the key file and its path), the database size, and a reminder to back up the key separately.

**Backend work needed:** `POST /api/settings/api-token/regenerate` (returns the new token once), `GET /api/settings/api-token` (`last4`, `source`: `env` or `stored`), and `GET /api/system` (`version`, `ffmpeg_version`, `libopus`, `audiobook_folder` with `free_bytes` and `total_bytes`, `key_source` and `key_file`, `database_bytes`). Low space means under 5% or under 50 GB free, whichever is smaller.

### Activity: books in a run (`expanded`)

Each run row has a chevron button that expands a row underneath, listing the run's books in three columns: Downloaded, Failed and Refused by Audible, each with a count. Every book links to its detail page, with a small cover and a note (time and size, or the error). Runs from before per-book records existed say so instead.

**Backend work needed:** a `sync_run_books` table (`run_id`, `book_id`, `outcome`: `downloaded`/`failed`/`refused`, `error`, `bytes`, `seconds`) written by `download_books`, and `GET /api/sync/runs/{id}/books`. This is the lowest priority.

### Common patterns (`Patterns.dc.html`), 404 and unreachable

- **Skeletons:** grey blocks in the shape of the content (a card with rows; the library grid's covers and two text lines), with a slow shimmer and none under `prefers-reduced-motion`. Set `aria-busy` on the container.
- **A failed request:** replaces only the card that failed. It shows a warning icon, a title saying what could not load, the API's `detail`, the request and status in small mono, and **Retry**.
- **Toasts:** bottom right, newest on top (full width at the bottom on a phone). A success toast closes itself after 4 s; an error toast stays until dismissed and has a title and the `detail`.
- **404** (`NotFound.dc.html`, `kind`): inside the normal layout, a large "404", a title ("This book is not in your library" or "Page not found"), a line of explanation, the path, and **Back to the library** and **Go to the dashboard**. Use it for unknown routes and for `GET /api/books/{id}` returning 404.
- **Service unreachable** (`Unreachable.dc.html`): full page, without the sidebar, shown when requests fail at the network level (no response at all, not an error status). It says the service is not answering, counts down to an automatic retry with backoff, offers **Retry now**, and shows two `docker compose` commands to check the container. It goes back to where you were when the service answers.

### Phone layout

At 720px and narrower the sidebar is hidden and a **56px top bar** takes its place: a menu button on the left, the logo and the page name, and your avatar on the right (linking to Your sign-in). The menu button opens the **sidebar as a drawer** from the left, `min(300px, 86vw)` wide, over a page dimmed to 60%. It has the same links, the current page highlighted, the service state, the username and Sign out, plus a close button beside the logo. The X, the backdrop and Escape close it, and so does picking a page. While it is open, focus moves into it and is trapped there, the page behind does not scroll, and the menu button has `aria-expanded="true"` and `aria-controls` pointing at the nav (variant `mobileMenu`).

Page padding drops to 20px top and 16px at the sides. The Dashboard totals and covers, and the Library grid, use `repeat(auto-fill/auto-fit, minmax(...))` so they drop columns by themselves (two covers across at 390px). Toolbars and button rows wrap; the Library table keeps its minimum width and scrolls sideways inside its card. The `Phone*` frames show the Dashboard, both Library views and the open menu at 390px, in dark and light.

## v3.4.0: Search and Wishlist

Two new screens, Search (`Search.dc.html`, `/search`) and Wishlist (`Wishlist.dc.html`, `/wishlist`), and a change to Library's search box. They go in the sidebar and the phone's drawer after Library: Search with a magnifier, Wishlist with a heart and the number of books on every wishlist. The `StateSearch*`, `StateWishlist*`, `StateLibraryPrefix*`, `Light*` and `Phone*` frames of the v3.4.0 row on the canvas show each variant switched on.

### A catalogue book

Search, a series, a book's panel and the Wishlist all show a catalogue book (`CatalogBookOut`) the same way: one row, not a grid, since a book carries more than a cover can.

- **Cover** 88px square (64 in a series, 40 for an edition, 160 in the panel), and the title with its subtitle after a colon. The cover and the title open the book's panel.
- **By** the authors (`authors[].name`) · **Narrated by** the narrators.
- **The series** from `primary_series`, as "Old Man's War, Book 1" (never `series[0]`); no line when there is none.
- **A line of facts:** length (`length_seconds` as "11h 12m"), "Released 2007" (the year; the full date in the panel), and the rating as "★ 4.6 (41,880)". A book with no ratings leaves the rating out.
- **The price**, at the right of the row (under it on a phone): the member price prominent (17px bold), the list price before it struck through and muted, and "or 1 credit" under them. Only one price when the two are equal or the member price is null; no price block at all when `price` is null. Amounts are in `price.currency`, formatted for the browser's locale.
- **Three markers**, small pills under the facts, always in this order:
  - **In library** (`in_library`): `--ok` text on `--ok` at 14%, a check, "In library →", linking to `/library/{library_book_id}`.
  - **On wishlist**: the accent heart, `--accent-text` on `--accent` at 14%. With several accounts it names the account: "On Alex (UK)'s wishlist"; with one, "On wishlist". A book on two accounts' wishlists shows one marker per account.
  - **Plus** (`plus`): an outline pill, `--text-2` on a `--line-strong` border, titled "Included in Audible Plus". It is information, not a status, so it takes no status colour.
- **The action** under the price: **Add to wishlist** (outline heart) or **Remove from wishlist** (filled heart in `--accent-text`), a secondary button. While the request is in flight it is disabled and busy ("Adding…", "Removing…", a spinner, `aria-busy`), and the marker changes only once the API has answered. **No Add for a book the account owns:** the API refuses it (a wishlist is for books not yet held), so a row whose book the account it would be added to holds (`library[].account_id`) has no button, and the panel says "Alex (UK) owns this book, so it cannot go on their wishlist." When another account of the marketplace holds it, Add is still offered, to the accounts that do not.

### The search box and its prefixes (Search and Library)

One parser in the web app for both pages, with the rules decided for v3.4.0: the prefixes are `author:`, `narrator:`, `series:` and `title:`; a value runs to the next prefix or the end, so no quotes; anything not after a prefix is keywords; only those four words count, so "Star Wars: Trials of the Jedi" is plain keywords. A prefix is matched case-insensitively at the start of a word.

- **The box keeps exactly what was typed**, `author:John Scalzi colonial`. It is not tokenised in place: editing text stays ordinary editing.
- **Search tips**, a small button inside the box at its right end, opens a popover (`tips` variant): a line on how a value runs, the four prefixes in mono accent with what each matches and an example, one combined example, and the note that only those four words count. Escape, the ×, or a click outside closes it. Under the box, a line of helper text repeats the four prefixes, and the box's placeholder names them too.
- **Chips** under the box, labelled "Searching for", show what the box was read as: one chip per prefix ("Author John Scalzi ×", the field muted and the value bold) and one for the keywords ("Keywords colonial ×", on a fainter border). The chips are 6px-radius tokens, not pills, so they never read as the status filters. Removing a chip takes that part out of the box and searches again; **Clear search** empties the box. No chips row when the box is empty.
- The query is in the URL as typed (`?q=author:John%20Scalzi%20colonial`), so a search can be bookmarked and survives a reload; it is debounced by about 300 ms, as on Library.

### Search (`Search.dc.html`)

`GET /api/catalog/search` with what the box was read into (`keywords`, `author`, `narrator`, `title`), `category_id`, `sort`, `page`, `page_size` (20) and `account_id`. A `series:` goes to the series view below instead.

- **Header:** "Search", and "The Audible catalogue on audible.co.uk, with Alex (UK)'s prices", from the account searched.
- **Toolbar:** the search box (it takes the free width); **Search in**, only when there are several signed-in accounts: each as "Alex (UK) · audible.co.uk", by default the API's own choice (the oldest signed in), since the account picks the marketplace and the member price; with one account there is no control at all. **Category**: "All categories" then the 24 or so top-level categories of `GET /api/catalog/categories`; choosing one adds a second select beside it for its children, starting at "All of Science Fiction & Fantasy". **Sort**: Relevance (the default), Bestsellers, Newest, Oldest, Title (`relevance`, `bestsellers`, `newest`, `oldest`, `title`). All in the URL.
- **Results:** a count line, "64 results on audible.co.uk" (`total`), then the rows in one card, then the pager: "1–20 of 64", Prev, page numbers, Next. A page may hold a few fewer than 20, as podcasts are left out; nothing says so.
- **Past what Audible pages** (`capped`): when `total` is above `reachable`, the count line adds "Audible shows only the first 1,000; narrow the search to see the rest"; the pager's last page is `reachable / page_size`, with Next disabled there, and under it a note with the warning border: "Showing the first 1,000 of 4,210 results. Audible pages no further. Narrow the search with a prefix or a category to see more." A page past `reachable` is a 422; a hand-edited URL that asks for one goes to the last reachable page.
- **Adding with several accounts:** Add to wishlist gets a chevron and opens a small menu (`action=addMenu`), "Add to the wishlist of", listing the signed-in accounts **of the searched marketplace**, the searched one first and marked "searched". A book and its ASIN belong to their marketplace, so an account on another one is not offered, and the menu's foot says so. When the marketplace has a single account, Add adds straight away without a menu, even with several accounts in all.
- **Removing:** Remove from wishlist takes the book off the wishlist the marker names, with no confirmation, since adding it back is one click. A book on two accounts' wishlists gets a menu of the two, like Add.
- **Feedback** (`action`): `adding` and `removing` show the busy button on that row only; `added` shows the marker and the Remove button, and a success toast "Added to Alex (UK)'s wishlist" (with one account, "Added to your wishlist"); `removed` likewise ("Removed from Alex (UK)'s wishlist"); `addFailed` and `removeFailed` put the button back as it was and show an error toast, "Could not add Zoe's Tale to the wishlist" with the API's `detail`.

**States** (`state`):

- `intro`, the first visit, an empty box: a card "Search the Audible catalogue", saying it searches everything Audible sells on the marketplace, not your library; that a book a library here holds is marked In library; and whose prices are shown. Beside it, the four prefixes with what each does, and three example searches as buttons (`author:Adrian Tchaikovsky`, `series:The Expanse`, `narrator:Ray Porter dragons`) that fill the box and search.
- `loading`: the results card as a skeleton of five rows (cover, three lines, a marker, the price and a button).
- `results`: as above.
- `noResults`: the filters stay as set; a card "No books match this search" restates it in words, "Nothing on audible.co.uk matches author John Scalzi with the keywords “zombies” in Science Fiction & Fantasy › Military.", and offers **Clear filters** (the box, the category and the sort). No pager.
- `noAccount`, when no account is signed in (none, or all `needs_login`): the toolbar is hidden, and a dashed card reads "No account to search with / Add an Audible account, or sign in to one, to search the catalogue", with **Go to Accounts**. Also the answer to the API's 409.
- `error`, Audible not answering (a 502): the usual failed-request card in place of the results, "Could not search the catalogue", the `detail`, `GET /api/catalog/search · 502`, and Retry. The toolbar stays usable.

### A book's panel (`detail`)

A side panel from the right, 520px wide over the dimmed page (the whole screen on a phone), opened from a cover or a title on Search, in a series or on the Wishlist. It reads `GET /api/catalog/books/{asin}` for the description; the row's data shows at once while it loads. The book is in the URL (`?book=B002V0QK4C`), so Back closes it and a link opens it. Escape, the × and the backdrop close it; focus is trapped inside while it is open.

- The marketplace as an eyebrow, the 160px cover, the title (22px bold), subtitle, authors, narrators, the series, and the markers.
- **"In your library"**, when held: a full-width link card, "In your library · Alex (UK), Downloaded", **Open →**, to the library book. It names the account and the book's status, from `library_book_id`.
- The price block (member price 20px, the list price struck through, "Member price · or 1 credit") and Add or Remove, as in the row.
- **Details:** length, released (the full date), language, publisher, Unabridged or Abridged (`abridged`; left out when null), genres, the rating "★ 4.6 from 41,880 ratings", and the ASIN in mono.
- **Description** from `description_html`, the allow-listed HTML as on the Book page, folded to about six lines with Show more.

### Series (`series`)

A `series:` in the box asks `GET /api/catalog/series?name=` (with `author`, when the box has one, to narrow it). Category and Sort are disabled, and the helper line says why: a series is shown in reading order. Other prefixes and keywords narrow the series' books in the browser.

- `choose`, several match: a card "3 series match “Old Man's War”", and one row per match, best first as the API orders them: the title, an "Exact match" pill on `exact` ones, and "6 books found" (`books_found`). The line under the heading says the counts are a rough guide, since they are the books a search for the name returned, not the size of the series. Choosing one shows it, and the series view keeps a "← 2 other series matched" link back.
- `one`, one match (straight away) or a choice: `GET /api/catalog/series/{asin}`. A card with the eyebrow "Series", the title, and "14 books in 7 positions, in reading order · 3 in a library here". One row per **position** (`sequence`: 1, 2, 2.5, 3 …), the position large in mono on the left, books with no position last.
  - **The edition shown** for a position: one a library here holds, else one on a wishlist, else the one with the most ratings. It is a full row: cover, title, byline, facts, markers, price, Add or Remove.
  - **"3 editions"**, a small outline button under it with a chevron (`aria-expanded`), when the position has more than one; open, it reads "Hide the other 2 editions" and lists them indented on a 2px rule. Each edition is one line: a 40px cover, its title as Audible writes it, **what differs** (narrator, length, release year, and a word when it can be told: "the same audio in two parts" for a "Part 1 of 2", "a new recording" for another narrator of the same title, "one of 13 episodes"), its markers, its member price and a small Add or Remove. One edition can be In library while another is not, which is the point of showing them.
  - Positions with several editions start folded, except the first one with more than one, which shows the pattern.
- None match: the `noResults` card, worded for a series: "No series called “…” was found on audible.co.uk."

### Wishlist (`Wishlist.dc.html`)

Every signed-in account's wishlist in one list. Each book is a catalogue book as above, plus the account whose wishlist it is on (a marketplace code chip and the name, only with several accounts) and "Added 10 Nov" (`Today` when recent). The same book on two accounts' wishlists is two rows, one per account.

- **Header:** "Wishlist", "23 books on the wishlists of 3 accounts" (with one, "23 books on your wishlist"). At the right: "Updated 2 hours ago", **Refresh from Audible** (a secondary button) and **Find books** (primary, to Search).
- **Refresh from Audible** re-reads every wishlist. While it runs (`refreshing`) the button is busy ("Refreshing…") and the line reads "Reading the wishlists from Audible…"; the list stays as it is until the answer replaces it. Otherwise the list is as of the last sync, which reads the wishlists, so it can be a few hours old.
- **Toolbar:** Account (All accounts, then each, only with several), Sort: Date added, newest first (the default), Title, Release date. Both in the URL. At the right, "2 already in a library", with the `--ok` dot.
- **In library, prominently:** a book a library here holds is the reason to remove it, so its row has a 3px `--ok` edge on the left and an `--ok` tint at 5%, and the marker sits above the title, larger, naming the library: "In Alex (US)'s library →" (with one account, "In your library →"), linking to the book.
- **Remove** on each row, a secondary button, never the destructive style. It confirms (`remove=confirm`): "Remove The Ghost Brigades from Alex (UK)'s wishlist?", "It comes off the wishlist on Audible too. You can add it back from Search.", **Keep it** and **Remove from wishlist** (the accent button). Then `removing`: the row dims and its button is busy; `removed`: the row goes and a toast says so; `failed`: the row is back as it was and an error toast reads "Could not remove The Ghost Brigades" with the `detail`.
- A book's cover and title open the same panel as on Search, for the account's marketplace.
- 50 rows a page, with the Library's pager when there are more.

**States** (`state` and the switches):

- `loading`: the toolbar, and five skeleton rows.
- `list`: as above.
- `empty`, every account's wishlist is empty: a dashed card, "Every wishlist is empty" (with one account, "Your wishlist is empty"), saying to search the catalogue and that books added on Audible itself show up after the next sync or straight away with Refresh, and **Search the catalogue**.
- `filterEmpty`, the account filter matches nothing although others have books: "Sam (UK)'s wishlist is empty", "The other accounts have 21 books on theirs.", **Show all accounts** and **Search the catalogue**. The toolbar stays.
- `partial`, one account's wishlist could not be read: a notice with the warning border above the list, "Alex (US)'s wishlist could not be read. Audible did not answer. The other accounts' wishlists are shown; Alex (US)'s books are as of the last read, 2 days ago.", and **Try again**. The "Updated" line names that account's own time. An account that needs a sign-in says so instead, linking to Accounts.
- `noAccount`: as on Search, "No wishlist to show / Add an Audible account, or sign in to one, to see its wishlist here", **Go to Accounts**.

### Library: the search box (`search`, `tips`)

- Library's box takes the same four prefixes, with the same Search tips popover and the same chips; its placeholder reads "Title, author or series, or author: narrator: series: title:". Plain words keep matching title, author or series (`q`); a prefix goes to `title`, `author`, `narrator` or `series` of `GET /api/books`.
- **Two rows of chips**, the search's first: when the box has a prefix, the chips row "Searching for" sits under the toolbar, and the status row under it gets a matching "Status" label in the same column, so the two read as two filters stacked, one from the box and one chosen. The search chips are square-ish tokens with ×; the status chips stay toggle pills. Without a prefix there is no chips row and no label, as before.
- The status chips' counts stay those of the library (`GET /api/stats`), not of the search, as now.
- **`noResults` with a prefix** (`StateLibraryPrefixNoResults`): both rows stay, and the card restates them: "Nothing matches author Mark Lawrence and series “Broken Empire” in Failed." **Clear filters** clears the box and the status.

### Backend work for v3.4.0

- **The wishlist** (`feat/wishlist`): `GET /api/wishlist` with `account_id`, `sort` and paging, each item a `CatalogBookOut` plus `account_id` and `added_at`, and per account when it was last read and whether that read failed (`accounts: [{id, read_at, error}]`), which the Updated line, the `partial` notice and the sidebar count need. `POST /api/wishlist` `{account_id, asin}` and `DELETE /api/wishlist/{account_id}/{asin}`, answering with the item or 404, and keeping the table in step at once, since Audible answers both silently. `POST /api/wishlist/refresh` re-reads every account's wishlist (or one, `account_id`).
- **The wishlist markers on catalogue books:** `CatalogBookOut` needs the accounts whose wishlist holds the book (`on_wishlist: [account_id]`), from our table, for Search, a series and the panel.
- **The library books behind In library:** `CatalogBookOut` lists every library here holding the book, `library: [{account_id, book_id, status}]`, so a row can name the account ("In Alex (US)'s library"), withhold Add from an account that owns the book, and the panel can say "Alex (UK), Downloaded" without another request. `in_library` and `library_book_id` stay, as the first of them.
- **Editions in parts:** `CatalogBookOut.multipart`, from Audible's `MultiPartBook`, for "the same audio in two parts": the titles of such editions do not say so (Old Man's War's do not).
- **The sidebar's Wishlist count:** a `wishlist` count in `GET /api/stats`, which the sidebar already reads.

## Behaviour common to every screen

- Show a skeleton while loading, a message with Retry when a request fails, and a short confirmation after an action (a toast is fine).
- Every error the API returns is `{"detail": "..."}`: show `detail`.
- Times come from the API in UTC; show them in the browser's time zone, relative when recent ("Today 08:00", "in 5h 40m").
- Destructive actions (delete files, remove an account) always confirm in a dialog. Nothing else does.
- Accessible as built: real buttons, links and labelled inputs; switches are `role="switch"` with `aria-checked`; icon-only buttons have an `aria-label`; dialogs trap focus and close on Escape; the live progress bar has `aria-valuenow`.
- Narrow screens: the sidebar stacks above the content, toolbars wrap, tables scroll sideways, grids drop columns.

## Backend work this needs

1. **Web app users:** create `admin` with a generated password on the first start and log it once; store a salted password hash (argon2 or bcrypt) encrypted like the other secrets; track whether the generated password is still in use.
2. **Sessions:** `POST /api/auth/login` (`username`, `password`, `remember`), `POST /api/auth/logout`, `GET /api/auth/me` (`username`, `generated_password`); an HTTP-only session cookie accepted by every route that now takes the bearer token; rate-limit failed sign-ins.
3. **Changing the sign-in:** `PUT /api/auth/username` and `PUT /api/auth/password`, each requiring the current password; a password change ends every other session.
4. **A way back from a forgotten password,** run on the server, since there is no email: for example a CLI command that resets the password and logs a new one. The sign-in screen's Forgot your password? card shows it: `docker compose exec audible-sync uv run --no-sync audible-sync reset-password`. Done: it refuses when the CLI holds another secret key than the service.
5. **Serving the app:** mount the built `web/dist` in `src/audible_sync/api/web.py` with the `index.html` fallback, after the `/api` routes.
6. **Docker:** a Node build stage that runs the `web/` build and copies `dist` into the image; `.dockerignore` for `web/node_modules`.
7. **CI:** lint, type-check and build `web/` beside the Python checks.
8. **Round 2:** `generated_password` on `GET /api/auth/me` (the dashboard banner); `POST /api/books/bulk`; the API-token endpoints and `GET /api/system`; optionally `GET /api/queue` for Up next; per-book run records and `GET /api/sync/runs/{id}/books` (lowest priority). Each is described under its screen in Round 2 above.
9. **v3.4.0:** the wishlist endpoints, the wishlist markers on catalogue books, the library books behind In library, editions in parts and the wishlist count, under [v3.4.0's backend work](#backend-work-for-v340).

Open question: `GET /api/books` has no per-book file size, only the totals in `/api/stats`, so the design shows no sizes per book.
