# Configuration

The `config/config.ini` file has the following sections and options.

The file is read **once**, on the first run, and copied into a `settings` table in the database. From then on the table is what the app runs on: `GET`/`PUT /api/settings` read and change it at runtime with no restart, and edits to the file are not picked up again. To start over from the file, empty the table and run the app:

```bash
uv run python -c "import sqlite3; c=sqlite3.connect('data/audible_sync.db'); c.execute('delete from settings'); c.commit()"
```

Every path setting may be absolute or relative. A relative path is resolved against the application folder — the repository root, or `/app` inside the Docker image — not the folder you happen to run the app from, so the app behaves the same started from anywhere. `~` is expanded, and an absolute path lets you file books outside the repository.

## `sync`

 - `max-download`: how many books to download and decrypt on each run (per account), leave unset to get everything waiting. A title Audible refuses to license does not use up a slot
 - `max-attempts`: how many times a book is downloaded before it is given up on and marked `failed`, default `3`
 - `max-consecutive-failures`: how many books in a row may be refused a licence or fail before a run gives up on the rest of the queue, default `5`; leave empty to never give up. A streak like that means the account or Audible is the problem, not the books - the run is recorded as `aborted` with the reason, and the next run tries again
 - `enabled`, `interval-minutes`: whether the background service runs syncs on a schedule, and how often (default `true`, `360`; at least `5`). The next run is the previous run's start plus the interval, whatever became of that run; a service that was down past its slot runs as soon as it starts. The one-shot CLI ignores both
 - `auto-monitor-new`: whether a purchase seen for the first time is queued for download, default `true`
 - `daily-full-refresh`: whether a run re-reads each account's whole library once a day, default `true`. An ordinary run reads only what was bought since the last one, so a publisher's later edit - a corrected title, a new cover, a description - would never reach the books already stored. With this on, a run is a full refresh for an account whose last one is more than a day old (counted per account, so one added later or one whose refresh failed is not skipped). It costs one request per 1000 books. **Refresh all from Audible** on the Dashboard, `POST /api/sync` with `{"full": true}` or `audible-sync --full` does one at any time. A refresh changes the database only: a book already downloaded keeps its folder and tags until it is downloaded again

## `folders`

 - `downloads`: folder where files are temporarily downloaded to before being converted. Default: `data/downloads`
 - `audiobooks`: folder to copy the converted audiobooks to. Default: `audiobooks`

Both are optional; leave the section out to keep the defaults.

## `naming`

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

## `encoding`

Optional, and off by default: unless you set it, the audio is kept exactly as Audible supplies it (AAC in an M4B container, no re-encoding).

 - `format`: `m4b` keeps the original AAC audio, no re-encode and no quality loss. `oga` re-encodes to Opus in an Ogg container, which is much smaller for speech at the same perceived quality. Default: `m4b`
 - `bitrate`: Opus bitrate in kbps, only used with `oga`, 1 to 256. `64` matches Audible's own quality; `32` to `48` is still very good for spoken word. Default: `64`

Both formats get the same metadata, cover art and chapters, and the publisher's summary as plain text: `description` in M4B (the `desc` atom), `SYNOPSIS` in Ogg, since there FFmpeg writes the comment carrying the book's ASIN as `DESCRIPTION`. In M4B files the series name, series position and author are stored as iTunes freeform tags (`----:com.apple.iTunes:series` and so on), which Audiobookshelf and most taggers read. In Ogg files the cover is stored as a `METADATA_BLOCK_PICTURE` tag and the chapters as `CHAPTERxxx` tags, which is what players such as Audiobookshelf, VLC and Foobar2000 expect. `oga` needs an FFmpeg 5 or newer built with libopus (`ffmpeg -encoders | grep libopus`); the Docker image has it. Changing the format only affects books downloaded afterwards.

## `notifications`

 - `webhook-url`: optional `http(s)` URL that receives a JSON `POST` after every run of every account - `{"event": "sync.finished", "run": {...the run row...}, "account": {"id", "name", "country_code"}}` - for example to make Audiobookshelf rescan. A receiver that is down is logged and ignored; the run is never held up. `POST /api/settings/webhook/test` sends a sample event. Unset by default

## Environment variables

The service reads these environment variables, whether it runs in Docker or not:

| Variable | Default | Meaning |
|----------|---------|---------|
| `AUDIBLE_SYNC_HOST` | `0.0.0.0` | Interface to listen on |
| `AUDIBLE_SYNC_PORT` | `8080` | Port |
| `AUDIBLE_SYNC_API_TOKEN` | generated | Bearer token the API requires; generated, stored and logged (once) on the first start if unset |
| `AUDIBLE_SYNC_CORS_ORIGINS` | none | Comma-separated origins allowed to call the API from a browser, e.g. a UI dev server. A listed origin may use the web app's sign-in cookie; `*` may not (any page on the host could then act as you), so it allows the bearer token only |
| `AUDIBLE_SYNC_WEB_DIR` | `web/dist` | The built web app, served at every path outside `/api`. Without an `index.html` there, only the API is served |
| `AUDIBLE_SYNC_SECURE_COOKIE` | `false` | `true` always marks the web app's session cookie `Secure`. Set it behind a reverse proxy that serves https: the service only sees plain http there, and otherwise marks the cookie `Secure` only for requests that arrive over https |
| `AUDIBLE_SYNC_BASE_PATH` | none | The path a reverse proxy serves the app under, e.g. `/audible-sync`; see [Behind a reverse proxy](installation.md#behind-a-reverse-proxy-under-a-path) |
| `AUDIBLE_SYNC_DEBUG` | `false` | Force DEBUG logging whatever the settings say |
| `AUDIBLE_SYNC_SECRET_KEY` | key file | The key the stored credentials and API token are encrypted with: 44 characters of URL-safe base64, as `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'` prints. Unset, the key file is used |
| `AUDIBLE_SYNC_SECRET_KEY_FILE` | `secrets/secret.key` | Where the key file is; generated owner-only on the first start if missing |
