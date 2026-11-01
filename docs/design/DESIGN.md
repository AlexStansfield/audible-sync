# Web app design (Milestone 4)

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
| `LightDashboard.dc.html`, `LightLibrary.dc.html` | The Dashboard and Library in the light theme | - |

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

**Polling `GET /api/status`:** every 2 s while `scheduler.running` is true, every 30 s otherwise, and once straight after any action that starts or stops a run. The dashboard's run card, the sidebar's service state and the library's Downloading badges all read this one query. When a run finishes, refetch the stats, the library and the runs.

## Screens

### Dashboard (`Main.dc.html`)

- **Header:** the number of accounts and the schedule; the next run time with a relative countdown (`scheduler.next_run_at`). **Sync now** (`POST /api/sync`) when idle; **Cancel run** (`POST /api/sync/cancel`) while running. A `409` means the state changed under you: refetch the status and show a brief message.
- **Run card**, only while a run is in flight (`current_run`): the cover and title of `current_run.book`, the stage (Syncing / Downloading / Cancelling), the run id, account and start time, a progress bar of `transfer.bytes / transfer.total` (indeterminate when `total` is null), and "Book `books_done` of `books_total` this run".
- **Totals** (`GET /api/stats`): In library, Downloaded (with bytes on disk), Waiting, Unavailable, Failed. Each tile opens the Library filtered to that status.
- **Accounts:** one row per account with its marketplace, totals, bytes on disk, last run, and a stacked bar of its books by status.
- **Recently downloaded:** six covers, newest first (`GET /api/books?status=downloaded&sort=downloaded_at`).
- **Needs attention:** shown only when there is something to show. Unavailable books with **Retry unavailable** (`POST /api/books/retry-unavailable`, then a message with `requeued`); failed books linking to the Library filtered to failed; an account that needs a sign-in, linking to Accounts.
- **Recent runs:** the last five (`GET /api/sync/runs?limit=5`), each linking to Activity.

### Library (`Library.dc.html`)

`GET /api/books` with `q`, `account_id`, `status`, `monitored`, `sort`, `order`, `page` and `page_size`. Keep every filter in the URL query string so a filtered view can be bookmarked and survives a reload.

- **Toolbar:** search (debounced by about 300 ms, over title, author and series), Account, Sort (date added, title, author, release date, downloaded), and a grid/table view switch stored in `localStorage`.
- **Status filter chips** with counts from the stats (All, Downloaded, Waiting, Downloading, Unavailable, Failed), and an "Unmonitored only" checkbox (`monitored=false`).
- **Grid:** square covers (`GET /api/books/{id}/cover`, lazy loaded) with a status badge, a progress strip on the book downloading, and title and author under it. The bookmark button toggles monitoring (`PATCH /api/books/{id}` with `{"monitored": bool}`). Update it optimistically and roll back on an error.
- **Table:** title with a small cover, author, series, account, status, added, and a monitored switch. It scrolls sideways on narrow screens.
- **Pagination:** "1-24 of 354", previous and next, page numbers.
- **Retry unavailable** in the header, with the count.
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
  2. **Code:** the five-letter `code` in large mono boxes, a link to `verification_url`, Copy code, and a live countdown to `expires_at`. Poll `GET /api/accounts/login/{login_id}` every 3 s: `confirmed` goes to step 3, `expired` offers a new code, `failed` shows `error`. A link switches to the browser sign-in for older accounts.
  3. **Browser fallback:** open `url` in a new tab, then paste the "page not found" address (`POST /api/accounts/login/{login_id}` with `response_url`). A `400` means paste again; a `404` means start again.
  4. **Connected:** the account name, and **Done** or **Sync now**.
- **Sign in again** opens the same dialog with the marketplace fixed and `account_id` set (with `deregister_previous: true`), and the confirmation says the library is kept and refused books are queued again.

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
4. **A way back from a forgotten password,** run on the server, since there is no email: for example a CLI command that resets the password and logs a new one. Not designed yet.
5. **Serving the app:** mount the built `web/dist` in `src/api.py` with the `index.html` fallback, after the `/api` routes.
6. **Docker:** a Node build stage that runs the `web/` build and copies `dist` into the image; `.dockerignore` for `web/node_modules`.
7. **CI:** lint, type-check and build `web/` beside the Python checks.

Open question: `GET /api/books` has no per-book file size, only the totals in `/api/stats`, so the design shows no sizes per book.
