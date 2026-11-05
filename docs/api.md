# API

Interactive documentation is served at `/docs` (OpenAPI at `/openapi.json`). Every request except the health check and signing in or out carries `Authorization: Bearer <token>`, or the session cookie that signing in sets; errors are always `{"detail": "..."}`.

| Method and path | What it does |
|-----------------|--------------|
| `GET /api/health` | `{"status": "ok", "version": ...}`, no token needed |
| `POST /api/auth/login` | Sign in to the web app with `username`, `password` and `remember`; sets an HTTP-only session cookie (30 days with `remember`, else until the browser closes). No token needed; a wrong username or password is a `401` that does not say which, and five failures in 15 minutes a `429` |
| `POST /api/auth/logout` | End the session and clear the cookie |
| `GET /api/auth/me` | `username` and `generated_password` (still using the generated one) |
| `PUT /api/auth/username` | Change the username; needs `current_password` (`403` when wrong) |
| `PUT /api/auth/password` | Change the password (12 characters or more); needs `current_password`. Every other session is signed out |
| `GET /api/status` | The schedule (`enabled`, `interval_minutes`, `next_run_at`, `running`), the run in flight (stage, current book, queue position, bytes of the current transfer) and the last run |
| `POST /api/sync` | Start a run now; `409` if one is already running |
| `POST /api/sync/cancel` | Stop the run in flight; `409` if there is none. The book being downloaded goes back to the queue |
| `GET /api/sync/runs?limit=&offset=&account_id=` | Run history, newest first, with a `total`; optionally one account's |
| `GET /api/sync/runs/{id}` | One run |
| `GET /api/sync/runs/{id}/books` | The run's books in three lists - `downloaded` (with size and time), `failed` and `refused` (with the error) - each in the order the run finished them; `recorded: false` for a run from before the per-book records |
| `GET /api/settings` | Every runtime setting |
| `PUT /api/settings` | Change some settings: send only the fields to change, `null` clears an optional one. A bad value is a `422` carrying the reason; the change applies to the next run |
| `GET /api/accounts` | Every account (never the credentials), with `needs_login` for one that has none |
| `GET /api/accounts/{id}` | One account |
| `PATCH /api/accounts/{id}` | Rename or enable/disable an account; a disabled account is skipped by the sync |
| `DELETE /api/accounts/{id}?deregister=true` | Remove an account with its library rows and run history; files on disk stay. `deregister` also removes the device from Amazon's device list first (best effort) |
| `GET /api/books?account_id=&status=&monitored=&q=&sort=&order=&page=&page_size=` | The library, paged (`items`, `total`, `page`, `page_size`). `q` matches title, author or series; `sort` is `date_added` (default), `title`, `author`, `release_date` or `downloaded_at` |
| `GET /api/books/{id}` | One book, with `primary_series` (the series it is filed under) worked out |
| `PATCH /api/books/{id}` | `{"monitored": bool}`: whether the book is wanted. An unmonitored book is never queued |
| `POST /api/books/{id}/delete-files` | Remove the audio, PDF, cover and annotations (and folders left empty) and unmonitor the book, so it stays deleted |
| `POST /api/books/{id}/redownload` | Remove the files and queue the book afresh, attempts reset |
| `POST /api/books/{id}/retry` | Queue a failed or parked book again, keeping any files; `409` for a downloaded book (use redownload) |
| `GET /api/queue?limit=` | What the next run will download, in order: each enabled account that can log in, oldest purchase first, up to `max-download` per account. `items` (`position`, `account_id`, `book`), `total` planned, `waiting` in those accounts in all, and `max_download`. A plan from the queue as it stands: a licence Audible refuses uses no slot, so a run reaches further down; a withdrawn title the run's sync restores takes a slot; and during a run the list starts with what that run has left |
| `POST /api/books/bulk` | `{"action", "ids" or "filter"}`: `monitor`, `unmonitor`, `retry` or `delete_files` on the books selected, or on everything a filter (`account_id`, `status`, `monitored`, `q`, as on `GET /api/books`) matches. Returns `updated`, `queued` and `skipped` with a reason each (`busy`, `downloaded`, `withdrawn`, `no_files`, `not_found`, `error`) |
| `POST /api/books/retry-unavailable` | `{"account_id"?}`: queue every parked book Audible still reports consumable again, for one account or all; returns `requeued`. For the day every licence was refused rather than one retired title - a re-login does this for its account by itself. Titles Audible withdrew are left to the sync, which restores them when they are offered again |
| `POST /api/books/{id}/refresh` | Re-read the book's details from Audible; `502` if Audible does not answer |
| `GET /api/books/{id}/cover` | The cover image: the downloaded file, or a redirect to Audible's copy before that |
| `POST /api/settings/webhook/test` | `{"url"?}`: send a sample event to `url`, or to the configured webhook; `502` with the reason if it fails |
| `GET /api/settings/api-token` | The API token masked to its last four characters (`last4`), and its `source`: `env` (`AUDIBLE_SYNC_API_TOKEN`) or `stored` |
| `POST /api/settings/api-token/regenerate` | Replace the token: the new one is returned this once, and the old one stops working at once. Only from a signed-in web app session (`403` with the bearer token alone, so a leaked token cannot rotate itself). `409` when `AUDIBLE_SYNC_API_TOKEN` sets it - change it there |
| `GET /api/system` | The app and FFmpeg versions and whether libopus is available, the audiobook folder's free and total space with a `low_space` flag (under 5% or 50 GB free, whichever is smaller), where the secret key comes from, and the database size |
| `GET /api/logs?limit=&level=` | The newest log lines (up to 1000 are kept), oldest first, at or above `level` |
| `GET /api/stats` | For a dashboard: per account and in total, books by status (`by_status`, and `monitored_by_status` for the monitored books alone), monitored/unmonitored, how many unavailable books `POST /api/books/retry-unavailable` would queue again (`retryable_unavailable`: licence refusals, not titles Audible withdrew), bytes on disk, the last run, and when the next one is due |
| `GET /api/marketplaces` | The Audible marketplaces you can sign in to: `country_code`, `domain`, `name` |
| `POST /api/accounts/login` | `{"country_code", "method"?, "name"?, "monitor_existing"?, "account_id"?, "deregister_previous"?}`: start signing in; `202` with the login. With the default `method` `code` it carries the `code` to enter at `verification_url` on any device, and the service polls Amazon until you have; with `browser` it carries the `url` to sign in at. `name` and `monitor_existing` describe the account to create; `account_id` instead replaces an existing account's credentials, keeping its library - the way back when Audible stops licensing a registration - with `deregister_previous` removing its old device from Amazon first. A login an account already holds (the same Amazon customer and marketplace) does the same without `account_id`, and the confirmed login says `replaced`; with an `account_id`, a login another account holds or a different Amazon customer's is refused, so the login ends `failed` and the account keeps working. `422` for an unknown marketplace, `404` for an unknown account, `502` when Amazon will not issue a code |
| `GET /api/accounts/login/{login_id}` | How the login is going: `status` is `pending`, then `confirmed` with the `account` (`replaced` when it already existed, `requeued` for how many of its parked books that queued), or `expired` (start again), `failed` with the `error`, or `cancelled`. A finished login stays readable for 15 minutes, then `404` |
| `DELETE /api/accounts/login/{login_id}` | Cancel a pending login: a code login stops polling Amazon, so a code entered afterwards does not add the account, and a browser login can no longer be completed; `200` with the login, now `cancelled`. One that has already ended is returned as it ended, so a code confirmed just before the cancel reads `confirmed` and the account stays. One being stored is waited for, up to 30 seconds; still `pending` after that means it can yet become the account. `404` once forgotten |
| `POST /api/accounts/login/{login_id}` | `{"response_url"}`: step two of a `browser` login, the address of the "page not found" page the browser lands on after signing in. `201` with the confirmed login; `404` if it expired or was already completed (start again), `400` if the address carries no code (paste again), `409` if Amazon accepted but the login could not be stored (another account holds it, it is a different customer's from the account's, the account has gone), `502` if Amazon rejected it |

Timestamps are ISO 8601 in UTC. The file actions refuse (`409`) a book a run is downloading; cancel the run first.

A ready-made request collection lives in [`opencollection/`](../opencollection/), one request per endpoint in the [OpenCollection](https://www.opencollection.com/) format: open the folder as a collection in [Bruno](https://www.usebruno.com) (or any client that reads the format), pick the `local` environment and set its `apiToken` secret to the token the service logged on its first start (or the one you set). Every request inherits the token except the health check.
