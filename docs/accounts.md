# Accounts

Start the service (see [Installation](installation.md)), open `http://localhost:8080` and sign in with the password the service printed on its first start. Then **Accounts → Add account**.

You add an Audible account by signing in to Amazon with a code, the way the Audible apps do. The app never sees your Amazon password: it asks Amazon for a five-letter code and shows it to you, you open `https://www.amazon.co.uk/code` (your marketplace's) on any device - a phone is fine - sign in if asked, and enter the code; the app notices within a few seconds and stores the credentials in the database. The code is good for ten minutes.

## From the command line

Without the web app:

```
python -m src.main login --marketplace uk [--name "Main"] [--no-download-existing] [--account ID] [--browser]
```

In Docker, put `docker compose exec audible-sync uv run --no-sync` in front of it.

Through the API (see [API](api.md)): `POST /api/accounts/login` gives the code, `GET /api/accounts/login/{login_id}` reports `confirmed` once you have entered it. `GET /api/marketplaces` lists the marketplaces (`uk`, `us`, `de`, `fr`, `ca`, `it`, `au`, `in`, `jp`, `es`, `br`).

## If the code is refused

If your Audible account predates Amazon sign-in and the code is refused, use the web app's **browser sign-in** link under the code, or add `--browser` (or `"method": "browser"`): you sign in to Amazon in your own browser at the address the app prints, land on a **"page not found"** page whose address carries a one-time code, and paste that address back (`POST /api/accounts/login/{login_id}`).

## Several accounts

The same person can have several accounts - one per Audible marketplace, e.g. a UK and a US library - each with its own credentials, library, sync cursor and run history. When an account is added you choose whether the books it already owns are queued for download (`monitor_existing`, the default) or left unmonitored so you pick them later; `--no-download-existing` on the command line. Purchases after that follow the `auto-monitor-new` setting.

## If Audible refuses every download

**Sign in again** on the account's card, or `--account ID` on the command line, signs in again for an existing account - its library and download history are kept - which is what to do if Audible starts answering every download with "License not granted to customer": Audible stopped licensing devices registered with the older app profile in October 2026, and a fresh sign-in registers one it accepts.

## Podcasts

Podcasts you follow on Audible are not synced or downloaded, only books are. A show stored by an earlier version is removed from the library on the next sync.
