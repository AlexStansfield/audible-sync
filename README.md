# Audible Sync

A python app that will fetch your library from audible, download the books and convert them to DRM free M4B or Ogg Opus files.

It runs as a background service: on a schedule (every six hours by default) it syncs the latest purchases from your audible library to the app database and downloads whatever is waiting. An HTTP API reports what it is doing, starts or cancels a run, shows the run history and changes the settings; the web UI that sits on top of it is the next milestone. A one-shot command line run is still there for scripting.

It records the download status of each book so will only fetch the missing books. A book that keeps failing is retried up to `max-attempts` times and then left alone, with the reason recorded, rather than re-downloaded on every run forever.

## Disclaimer

This app DOES NOT crack DRM. If that's what you're after, look elsewhere. 

What this app does do is decrypt the audiobooks that you have access to in your Audible library. It can only de-drm your books, no one elses.

The purpose of this app is to allow the user more control over how they consumer the books that they've paid for. I will never be adding any features to download books that are not in your library so please don't ask.

## Initial Setup

Add your Audible account by signing in through your own browser. The app never sees your Amazon password: it builds a sign-in address, you sign in to Amazon there (captcha and two-factor included), and Amazon sends the browser to a **"page not found"** page whose address carries a one-time code. Paste that address back and the app exchanges it for credentials, which are stored in the database.

From the command line:

```
python -m src.main login --marketplace uk
```

Or through the API (see below): `POST /api/accounts/login` gives the address, `POST /api/accounts/login/{login_id}` takes the pasted one. `GET /api/marketplaces` lists the marketplaces (`uk`, `us`, `de`, `fr`, `ca`, `it`, `au`, `in`, `jp`, `es`, `br`).

The same person can have several accounts - one per Audible marketplace, e.g. a UK and a US library - each with its own credentials, library, sync cursor and run history. When an account is added you choose whether the books it already owns are queued for download (`monitor_existing`, the default) or left unmonitored so you pick them later; `--no-download-existing` on the command line. Purchases after that follow the `auto-monitor-new` setting.

### Already have an audible-cli auth file?

If you have an `audible.json` from [audible-cli](https://github.com/mkb79/audible-cli) at the default `~/.audible/audible.json` (or at `audible-auth-file` in `config.ini`), the first start imports it as an account and does not read it again; from then on accounts are managed through the API. Another file is added with `POST /api/accounts/import`.

## Config

The `config/config.ini` file has the following sections and options.

The file is read **once**, on the first run, and copied into a `settings` table in the database. From then on the table is what the app runs on: `GET`/`PUT /api/settings` read and change it at runtime with no restart, and edits to the file are not picked up again. To start over from the file, empty the table and run the app:

```bash
uv run python -c "import sqlite3; c=sqlite3.connect('data/audible_sync.db'); c.execute('delete from settings'); c.commit()"
```

Every path setting may be absolute or relative. A relative path is resolved against the application folder — the repository root, or `/app` inside the Docker image — not the folder you happen to run the app from, so the app behaves the same started from anywhere. `~` is expanded, and an absolute path lets you file books outside the repository.

### `sync`

 - `max-download`: total number of books to download and decrypt on each app run, leave unset to get everything waiting to be downloaded
 - `max-attempts`: how many times a book is downloaded before it is given up on and marked `failed`, default `3`. A book Audible will not license is failed on the first try, since it cannot succeed later
 - `audible-auth-file`: path to the audible auth json, leave unset to default to `$HOME/.audible/audible.json`
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

 - `webhook-url`: optional `http(s)` URL the background service posts a JSON summary to after each sync run. Unset by default

## Running

### Docker

The simplest of all, copy the `compose.yml` file from the repository to the location of your choice.

Update the volume mounts to match where you want files to be stored.

If you want your own config then uncomment the volume mount line and place an updated copy of the `config.ini` file in the host side folder.

Then start the service:

```
docker compose up -d
```

It listens on port 8080. Every API call except the health check needs a bearer token: set `AUDIBLE_SYNC_API_TOKEN` in the environment (or a `.env` file next to `compose.yml`) to choose one, or leave it unset and the service generates one on its first start, keeps it, and prints it in the log:

```
docker compose logs audible-sync | grep "API token"
```

The first run starts as soon as the service does, then every `interval-minutes` (six hours by default). `docker stop` cancels a run in progress cleanly: the book being downloaded goes back to the queue.

For a single sync-and-exit instead of the service:

```
docker compose run --rm audible-sync uv run python -m src.main
```

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
python -m src.main login --marketplace uk [--name "Main"] [--no-download-existing]
```

The service reads these environment variables:

| Variable | Default | Meaning |
|----------|---------|---------|
| `AUDIBLE_SYNC_HOST` | `0.0.0.0` | Interface to listen on |
| `AUDIBLE_SYNC_PORT` | `8080` | Port |
| `AUDIBLE_SYNC_API_TOKEN` | generated | Bearer token the API requires; generated, stored and logged on the first start if unset |
| `AUDIBLE_SYNC_CORS_ORIGINS` | none | Comma-separated origins allowed to call the API from a browser, e.g. a UI dev server |
| `AUDIBLE_SYNC_DEBUG` | `false` | Force DEBUG logging whatever the settings say |

## API

Interactive documentation is served at `/docs` (OpenAPI at `/openapi.json`). Every request except the health check carries `Authorization: Bearer <token>`; errors are always `{"detail": "..."}`.

| Method and path | What it does |
|-----------------|--------------|
| `GET /api/health` | `{"status": "ok", "version": ...}`, no token needed |
| `GET /api/status` | The schedule (`enabled`, `interval_minutes`, `next_run_at`, `running`), the run in flight (stage, current book, queue position, bytes of the current transfer) and the last run |
| `POST /api/sync` | Start a run now; `409` if one is already running |
| `POST /api/sync/cancel` | Stop the run in flight; `409` if there is none. The book being downloaded goes back to the queue |
| `GET /api/sync/runs?limit=&offset=&account_id=` | Run history, newest first, with a `total`; optionally one account's |
| `GET /api/sync/runs/{id}` | One run |
| `GET /api/settings` | Every runtime setting |
| `PUT /api/settings` | Change some settings: send only the fields to change, `null` clears an optional one. A bad value is a `422` carrying the reason; the change applies to the next run |
| `GET /api/accounts` | Every account (never the credentials), with `needs_login` for one that has none |
| `GET /api/accounts/{id}` | One account |
| `PATCH /api/accounts/{id}` | Rename or enable/disable an account; a disabled account is skipped by the sync |
| `DELETE /api/accounts/{id}?deregister=true` | Remove an account with its library rows and run history; files on disk stay. `deregister` also removes the device from Amazon's device list first (best effort) |
| `POST /api/accounts/import` | `{"path", "name"?, "monitor_existing"?}`: create an account from an auth file the service can read |
| `GET /api/books?account_id=&status=&monitored=&q=&sort=&order=&page=&page_size=` | The library, paged (`items`, `total`, `page`, `page_size`). `q` matches title, author or series; `sort` is `date_added` (default), `title`, `author`, `release_date` or `downloaded_at` |
| `GET /api/books/{id}` | One book, with `primary_series` (the series it is filed under) worked out |
| `PATCH /api/books/{id}` | `{"monitored": bool}`: whether the book is wanted. An unmonitored book is never queued |
| `POST /api/books/{id}/delete-files` | Remove the audio, PDF, cover and annotations (and folders left empty) and unmonitor the book, so it stays deleted |
| `POST /api/books/{id}/redownload` | Remove the files and queue the book afresh, attempts reset |
| `POST /api/books/{id}/retry` | Queue a failed or parked book again, keeping any files; `409` for a downloaded book (use redownload) |
| `POST /api/books/{id}/refresh` | Re-read the book's details from Audible; `502` if Audible does not answer |
| `GET /api/books/{id}/cover` | The cover image: the downloaded file, or a redirect to Audible's copy before that |
| `GET /api/marketplaces` | The Audible marketplaces you can sign in to: `country_code`, `domain`, `name` |
| `POST /api/accounts/login` | `{"country_code"}`: step one of signing in. Returns `login_id`, the `url` to open in a browser, and `expires_at` (15 minutes) |
| `POST /api/accounts/login/{login_id}` | `{"response_url", "name"?, "monitor_existing"?}`: step two. `response_url` is the address of the "page not found" page the browser lands on after signing in. `201` with the new account; `404` if the login expired (start again), `400` if the address carries no code, `502` if Amazon rejected it |

Timestamps are ISO 8601 in UTC. The file actions refuse (`409`) a book a run is downloading; cancel the run first.

## Todo

There is plenty left to do, my ultimate goal is to have a web based service that can automatically sync, download and convert your books. 

Done so far: re-encoding to Ogg Opus to reduce size, PDF, cover art, chapters and metadata. Next up is turning it into a service with a web UI.

The current todo list can be found [here](todo.md).