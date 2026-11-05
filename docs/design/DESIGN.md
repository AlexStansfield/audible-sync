# Web app design (Milestone 4)

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
| `Profile.dc.html` | Your sign-in (change username and password) | `/profile` |
| `NotFound.dc.html` | 404 for an unknown route or a book that does not exist (`kind`) | any unknown path |
| `Unreachable.dc.html` | The service cannot be reached at all | - |
| `Patterns.dc.html` | Common patterns: skeletons, a failed request, toasts | - |
| `State*.dc.html`, `Phone*.dc.html`, `Light*.dc.html` | Frames that show a screen above with one variant switched on (first run, empty library, bulk selection, each Accounts dialog, phone width, light theme). They contain no design of their own: read the screen they import and its variant | - |

Every screen's `data-props` lists its variants. Round 2 added: Dashboard `firstRun`, `generatedPassword`; Library `empty` (`noBooks`, `noResults`) and `bulk` (`selecting`, `confirmDelete`, `result`); Book `status` = `downloading`, `monitored`, `sizeKnown`, `noRun`; Accounts `step` = `expired`, `failed`, `relogin`, `rename`, `remove`, `menu`; Sign in `forgot`; Settings `token` (`masked`, `confirm`, `regenerated`), `lowDisk`; Activity `expanded`.

## Stack

- **Vite + React + TypeScript**, a single-page app in `web/`. Client-side routing.
- **Built into the Docker image and served by FastAPI** at `/`, so there is one container, one port and no CORS in production. API routes stay under `/api`; every other path that is not a file serves `index.html` so client routes survive a reload.
- **Development:** the Vite dev server proxies `/api` to the service, or the service sets `AUDIBLE_SYNC_CORS_ORIGINS` to the dev server's origin.
- **Server state:** a query library (TanStack Query or similar) so one polled `GET /api/status` feeds every screen that shows the run in flight.
- **Types:** generate them from `/openapi.json` rather than writing them by hand; `src/schemas.py` is the contract.
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

Sidebar: the logo and version (from `GET /api/health`); Dashboard, Library (with the total book count), Activity, Accounts (with a red dot when an account needs a sign-in), Settings; at the foot, the service state (healthy, or "Syncing · book 3 of 10" while a run is in flight), the username linking to Your sign-in, and Sign out.

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

- **Toolbar:** search (debounced by about 300 ms, over title, author and series), Account, Sort (date added, title, author, release date, downloaded), and a grid/table view switch stored in `localStorage`.
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
| Folders and naming | `audiobook_folder`, `download_folder`, `folder_template`, `filename_template`; buttons that insert a placeholder at the cursor; a live preview of two sample paths (one series book, one not) worked out in the browser with the same rules as `src/naming.py` |
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

"Forgot your password?" sits beside the Password label. It swaps the form for a card: "Reset your password", an explanation that there is no email so the reset is done on the server, a code block with the command - `docker compose exec audible-sync uv run --no-sync python -m src.main reset-password` (`python -m src.main reset-password` outside Docker; `--username` also puts the name back to `admin`) - what happens next (sign in, choose your own password; every browser is signed out), and **Back to sign in**.

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
4. **A way back from a forgotten password,** run on the server, since there is no email: for example a CLI command that resets the password and logs a new one. The sign-in screen's Forgot your password? card shows it: `docker compose exec audible-sync uv run --no-sync python -m src.main reset-password`. Done: it refuses when the CLI holds another secret key than the service.
5. **Serving the app:** mount the built `web/dist` in `src/api.py` with the `index.html` fallback, after the `/api` routes.
6. **Docker:** a Node build stage that runs the `web/` build and copies `dist` into the image; `.dockerignore` for `web/node_modules`.
7. **CI:** lint, type-check and build `web/` beside the Python checks.
8. **Round 2:** `generated_password` on `GET /api/auth/me` (the dashboard banner); `POST /api/books/bulk`; the API-token endpoints and `GET /api/system`; optionally `GET /api/queue` for Up next; per-book run records and `GET /api/sync/runs/{id}/books` (lowest priority). Each is described under its screen in Round 2 above.

Open question: `GET /api/books` has no per-book file size, only the totals in `/api/stats`, so the design shows no sizes per book.
