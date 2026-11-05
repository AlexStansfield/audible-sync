# Audible Sync

A python app that will fetch your library from audible, download the books and convert them to DRM free M4B or Ogg Opus files.

It runs as a background service: on a schedule (every six hours by default) it syncs the latest purchases from your audible library to the app database and downloads whatever is waiting. An HTTP API reports what it is doing, starts or cancels a run, shows the run history and changes the settings; the web UI that sits on top of it is the next milestone. A one-shot command line run is still there for scripting.

It records the download status of each book so will only fetch the missing books. A book that keeps failing is retried up to `max-attempts` times and then left alone, with the reason recorded, rather than re-downloaded on every run forever.

## Disclaimer

This app DOES NOT crack DRM. If that's what you're after, look elsewhere. 

What this app does do is decrypt the audiobooks that you have access to in your Audible library. It can only de-drm your books, no one elses.

The purpose of this app is to allow the user more control over how they consumer the books that they've paid for. I will never be adding any features to download books that are not in your library so please don't ask.

## Initial Setup

Add your Audible account by signing in to Amazon with a code, the way the Audible apps do. The app never sees your Amazon password: it asks Amazon for a five-letter code and shows it to you, you open `https://www.amazon.co.uk/code` (your marketplace's) on any device - a phone is fine - sign in if asked, and enter the code; the app notices within a few seconds and stores the credentials in the database. The code is good for ten minutes.

From the command line:

```
python -m src.main login --marketplace uk
```

Or through the API (see below): `POST /api/accounts/login` gives the code, `GET /api/accounts/login/{login_id}` reports `confirmed` once you have entered it. `GET /api/marketplaces` lists the marketplaces (`uk`, `us`, `de`, `fr`, `ca`, `it`, `au`, `in`, `jp`, `es`, `br`).

If your Audible account predates Amazon sign-in and the code is refused, add `--browser` (or `"method": "browser"`): you sign in to Amazon in your own browser at the address the app prints, land on a **"page not found"** page whose address carries a one-time code, and paste that address back (`POST /api/accounts/login/{login_id}`).

The same person can have several accounts - one per Audible marketplace, e.g. a UK and a US library - each with its own credentials, library, sync cursor and run history. When an account is added you choose whether the books it already owns are queued for download (`monitor_existing`, the default) or left unmonitored so you pick them later; `--no-download-existing` on the command line. Purchases after that follow the `auto-monitor-new` setting.

## Config

The `config/config.ini` file has the following sections and options.

The file is read **once**, on the first run, and copied into a `settings` table in the database. From then on the table is what the app runs on: `GET`/`PUT /api/settings` read and change it at runtime with no restart, and edits to the file are not picked up again. To start over from the file, empty the table and run the app:

```bash
uv run python -c "import sqlite3; c=sqlite3.connect('data/audible_sync.db'); c.execute('delete from settings'); c.commit()"
```

Every path setting may be absolute or relative. A relative path is resolved against the application folder — the repository root, or `/app` inside the Docker image — not the folder you happen to run the app from, so the app behaves the same started from anywhere. `~` is expanded, and an absolute path lets you file books outside the repository.

### `sync`

 - `max-download`: how many books to download and decrypt on each run (per account), leave unset to get everything waiting. A title Audible refuses to license does not use up a slot
 - `max-attempts`: how many times a book is downloaded before it is given up on and marked `failed`, default `3`
 - `max-consecutive-failures`: how many books in a row may be refused a licence or fail before a run gives up on the rest of the queue, default `5`; leave empty to never give up. A streak like that means the account or Audible is the problem, not the books - the run is recorded as `aborted` with the reason, and the next run tries again
 - `enabled`, `interval-minutes`: whether the background service runs syncs on a schedule, and how often (default `true`, `360`; at least `5`). The next run is the previous run's start plus the interval, whatever became of that run; a service that was down past its slot runs as soon as it starts. The one-shot CLI ignores both
 - `auto-monitor-new`: whether a purchase seen for the first time is queued for download, default `true`

### `folders`

 - `downloads`: folder where files are temporarily downloaded to before being converted. Default: `data/downloads`
 - `audiobooks`: folder to copy the converted audiobooks to. Default: `audiobooks`

Both are optional; leave the section out to keep the defaults.

### `naming`

Controls where each converted book is filed inside the `audiobooks` folder and what the files are called. Both options are templates. Leave the section out to keep the default layout.

 - `folder`: folder for each book, relative to `audiobooks`. Default: `{author}/[{series}/][{sequence} - ]{title}`
 - `filename`: file name without the extension. The PDF, cover (`_cover.jpg`) and annotations (`_annotations.json`) use the same name. Default: `{title}`

Text inside square brackets is only included when every placeholder inside it has a value, so with the default template a book that is not part of a series is filed as `{author}/{title}`. A folder segment that ends up empty is skipped. Every value is made file-system safe before use: `:` becomes ` -`, `/` becomes `-`, and characters that are invalid on Windows are removed.

| Placeholder | Value |
|-------------|-------|
| `{author}` | first author (`Unknown Author` if none) |
| `{authors}` | all authors, comma separated |
| `{narrator}` | first narrator |
| `{narrators}` | all narrators, comma separated |
| `{title}` | book title |
| `{subtitle}` | book subtitle, often empty |
| `{series}` | series name, empty if the book is not in a series |
| `{sequence}` | position in the series, for example `1` |
| `{year}` | release year |
| `{asin}` | Audible identifier |

With the defaults, *One Word Kill* (book 1 of the Nick Hayes Series) becomes `Mark Lawrence/Nick Hayes Series/1 - One Word Kill/One Word Kill.m4b`.

An unknown placeholder stops the app at startup with an error naming it. Changing the templates only affects books downloaded afterwards; existing files are not renamed.

### `encoding`

Optional, and off by default: unless you set it, the audio is kept exactly as Audible supplies it (AAC in an M4B container, no re-encoding).

 - `format`: `m4b` keeps the original AAC audio, no re-encode and no quality loss. `oga` re-encodes to Opus in an Ogg container, which is much smaller for speech at the same perceived quality. Default: `m4b`
 - `bitrate`: Opus bitrate in kbps, only used with `oga`, 1 to 256. `64` matches Audible's own quality; `32` to `48` is still very good for spoken word. Default: `64`

Both formats get the same metadata, cover art and chapters. In M4B files the series name, series position and author are stored as iTunes freeform tags (`----:com.apple.iTunes:series` and so on), which Audiobookshelf and most taggers read. In Ogg files the cover is stored as a `METADATA_BLOCK_PICTURE` tag and the chapters as `CHAPTERxxx` tags, which is what players such as Audiobookshelf, VLC and Foobar2000 expect. `oga` needs an FFmpeg 5 or newer built with libopus (`ffmpeg -encoders | grep libopus`); the Docker image has it. Changing the format only affects books downloaded afterwards.

### `notifications`

 - `webhook-url`: optional `http(s)` URL that receives a JSON `POST` after every run of every account - `{"event": "sync.finished", "run": {...the run row...}, "account": {"id", "name", "country_code"}}` - for example to make Audiobookshelf rescan. A receiver that is down is logged and ignored; the run is never held up. `POST /api/settings/webhook/test` sends a sample event. Unset by default

## Running

### Docker

The simplest of all, copy the `compose.yml` file from the repository to the location of your choice.

Update the volume mounts to match where you want files to be stored.

If you want your own config then uncomment the volume mount line and place an updated copy of the `config.ini` file in the host side folder.

Then start the service:

```
docker compose up -d
```

It listens on port 8080. Every API call except the health check needs a bearer token: set `AUDIBLE_SYNC_API_TOKEN` in the environment (or a `.env` file next to `compose.yml`) to choose one, or leave it unset and the service generates one on its first start, keeps it, and prints it in the log **that once** - later starts log only its last four characters, so a log shared in a bug report does not give away the API:

```
docker compose logs audible-sync | grep "API token"
```

Lost it? Set `AUDIBLE_SYNC_API_TOKEN` to a token of your choosing and restart; it takes precedence over the stored one.

The web app has its own sign-in: one user, created on the first start as `admin` with a generated password that is printed in the log that once, the same way:

```
docker compose logs audible-sync | grep "Web app sign-in"
```

Sign in with it and choose your own. Forgotten the password? Reset it on the server; the new one is printed and every browser is signed out:

```
docker compose exec audible-sync uv run --no-sync python -m src.main reset-password
```

Add `--username` to set the username back to `admin` as well. Run it in the container as shown: a command run on the host with another secret key is refused, because the password it stored would be unreadable to the service.

The Audible credentials, the API token and the web app password's hash are stored encrypted. The key is generated on the first start into `./secrets/secret.key` (or set `AUDIBLE_SYNC_SECRET_KEY` to choose one). **Back up `./data` and `./secrets` separately**: a copy of `./data` on its own is not a working Audible login, which is the point, and `./secrets` is what turns it back into one. Lose the key and the accounts show as needing a login; sign in again and carry on.

The first run starts as soon as the service does, then every `interval-minutes` (six hours by default). `docker stop` cancels a run in progress cleanly: the book being downloaded goes back to the queue.

For a single sync-and-exit instead of the service:

```
docker compose run --rm audible-sync uv run --no-sync python -m src.main
```

### Developing the web app

The web app lives in `web/` (Vite, React, TypeScript; Node 22.12 or newer). Run the service as below, then in another terminal:

```
cd web
npm install
npm run dev        # http://localhost:5173, proxying /api to the service on :8080
```

`AUDIBLE_SYNC_API=http://host:port npm run dev` points the proxy elsewhere. The checks CI runs are `npm run lint`, `npm run typecheck`, `npm test` and `npm run build`; `npm run format` fixes the formatting. After changing `src/schemas.py` or a route, run `npm run gen:api` to regenerate `web/openapi.json` and the client's types in `web/src/api/schema.d.ts` (it needs `uv`); CI fails while they are stale. `npm run build` writes `web/dist`, which the service serves when it finds it.

### Running the App with Python (No Docker)

If you'd rather run the app directly in Python (e.g. for development or scripting), follow these steps:

---

#### ✅ 1. Clone the Repository

```bash
git clone https://github.com/alexstansfield/audible-sync.git
cd audible-sync
```

#### ✅ 2. Create and Activate a Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate
```

#### ✅ 3. Install Dependencies

```
pip install -r requirements.txt
```

#### ✅ 4. Install FFmpeg

This app requires [FFmpeg](https://ffmpeg.org/) to be installed and available on your system. For the `oga` output format it must be version 5 or newer and built with libopus, which the packages below all are.

##### macOS

```
brew install ffmpeg
```

##### Ubuntu/Debian

```
sudo apt install ffmpeg
```

##### Windows

1. Download the FFmpeg executable from [here](https://ffmpeg.org/download.html).
2. Extract the downloaded files.
3. Add the bin/ folder inside the extracted directory to your system's PATH.

#### ✅ 5. Run

The service:

```
python -m src.service
```

or a single sync-and-exit:

```
python -m src.main
```

or add an account:

```
python -m src.main login --marketplace uk [--name "Main"] [--no-download-existing] [--account ID] [--browser]
```

`--account ID` signs in again for an existing account - its library and download history are kept - which is what to do if Audible starts answering every download with "License not granted to customer": Audible stopped licensing devices registered with the older app profile in October 2026, and a fresh sign-in registers one it accepts.

The service reads these environment variables:

| Variable | Default | Meaning |
|----------|---------|---------|
| `AUDIBLE_SYNC_HOST` | `0.0.0.0` | Interface to listen on |
| `AUDIBLE_SYNC_PORT` | `8080` | Port |
| `AUDIBLE_SYNC_API_TOKEN` | generated | Bearer token the API requires; generated, stored and logged (once) on the first start if unset |
| `AUDIBLE_SYNC_CORS_ORIGINS` | none | Comma-separated origins allowed to call the API from a browser, e.g. a UI dev server. A listed origin may use the web app's sign-in cookie; `*` may not (any page on the host could then act as you), so it allows the bearer token only |
| `AUDIBLE_SYNC_WEB_DIR` | `web/dist` | The built web app, served at every path outside `/api`. Without an `index.html` there, only the API is served |
| `AUDIBLE_SYNC_SECURE_COOKIE` | `false` | `true` always marks the web app's session cookie `Secure`. Set it behind a reverse proxy that serves https: the service only sees plain http there, and otherwise marks the cookie `Secure` only for requests that arrive over https |
| `AUDIBLE_SYNC_BASE_PATH` | none | The path a reverse proxy serves the app under, e.g. `/audible-sync`; see below |
| `AUDIBLE_SYNC_DEBUG` | `false` | Force DEBUG logging whatever the settings say |
| `AUDIBLE_SYNC_SECRET_KEY` | key file | The key the stored credentials and API token are encrypted with: 44 characters of URL-safe base64, as `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'` prints. Unset, the key file is used |
| `AUDIBLE_SYNC_SECRET_KEY_FILE` | `secrets/secret.key` | Where the key file is; generated owner-only on the first start if missing |

### Behind a reverse proxy, under a path

To serve the app at `https://my.domain/audible-sync/` rather than at the root of a domain, set `AUDIBLE_SYNC_BASE_PATH=/audible-sync` and `AUDIBLE_SYNC_SECURE_COOKIE=true` (the proxy serves https; the service only sees http). The proxy may strip the prefix or pass it on; the service accepts either. The web app is the same build either way: the service tells it the path when it serves the page, and scopes the sign-in cookie to it. `/docs` is then at `/audible-sync/docs`. The path cannot start with one of the app's own (`/api`, `/assets`, `/docs`, `/library`, `/settings` and the other pages); the service refuses to start with one.

nginx, stripping the prefix (the trailing `/` on both lines matters):

```nginx
location /audible-sync/ {
    proxy_pass http://audible-sync:8080/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Traefik, passing it on (labels on the container):

```yaml
labels:
  - traefik.http.routers.audible-sync.rule=Host(`my.domain`) && PathPrefix(`/audible-sync`)
  - traefik.http.services.audible-sync.loadbalancer.server.port=8080
```

A Traefik `StripPrefix` middleware works too. Without `AUDIBLE_SYNC_BASE_PATH`, the app only works at the root of a domain.

## API

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
| `GET /api/stats` | For a dashboard: per account and in total, books by status, monitored/unmonitored, bytes on disk, the last run, and when the next one is due |
| `GET /api/marketplaces` | The Audible marketplaces you can sign in to: `country_code`, `domain`, `name` |
| `POST /api/accounts/login` | `{"country_code", "method"?, "name"?, "monitor_existing"?, "account_id"?, "deregister_previous"?}`: start signing in; `202` with the login. With the default `method` `code` it carries the `code` to enter at `verification_url` on any device, and the service polls Amazon until you have; with `browser` it carries the `url` to sign in at. `name` and `monitor_existing` describe the account to create; `account_id` instead replaces an existing account's credentials, keeping its library - the way back when Audible stops licensing a registration - with `deregister_previous` removing its old device from Amazon first. `422` for an unknown marketplace, `404` for an unknown account, `502` when Amazon will not issue a code |
| `GET /api/accounts/login/{login_id}` | How the login is going: `status` is `pending`, then `confirmed` with the `account`, or `expired` (start again) or `failed` with the `error`. A finished login stays readable for 15 minutes, then `404` |
| `POST /api/accounts/login/{login_id}` | `{"response_url"}`: step two of a `browser` login, the address of the "page not found" page the browser lands on after signing in. `201` with the confirmed login; `404` if it expired or was already completed (start again), `400` if the address carries no code (paste again), `502` if Amazon rejected it |

Timestamps are ISO 8601 in UTC. The file actions refuse (`409`) a book a run is downloading; cancel the run first.

A ready-made request collection lives in [`opencollection/`](opencollection/), one request per endpoint in the [OpenCollection](https://www.opencollection.com/) format: open the folder as a collection in [Bruno](https://www.usebruno.com) (or any client that reads the format), pick the `local` environment and set its `apiToken` secret to the token the service logged on its first start (or the one you set). Every request inherits the token except the health check.

## Todo

There is plenty left to do, my ultimate goal is to have a web based service that can automatically sync, download and convert your books. 

Done so far: re-encoding to Ogg Opus to reduce size, PDF, cover art, chapters and metadata. Next up is turning it into a service with a web UI.

The current todo list can be found [here](todo.md).