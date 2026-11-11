# Installation

## Docker

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

Open `http://localhost:8080`, sign in with it, and choose your own with **Change password** on the Dashboard. Forgotten the password? Reset it on the server; the new one is printed and every browser is signed out:

```
docker compose exec audible-sync uv run --no-sync audible-sync reset-password
```

Add `--username` to set the username back to `admin` as well. Run it in the container as shown: a command run on the host with another secret key is refused, because the password it stored would be unreadable to the service.

The Audible credentials, the API token and the web app password's hash are stored encrypted. The key is generated on the first start into `./secrets/secret.key` (or set `AUDIBLE_SYNC_SECRET_KEY` to choose one). **Back up `./data` and `./secrets` separately**: a copy of `./data` on its own is not a working Audible login, which is the point, and `./secrets` is what turns it back into one. Lose the key and the accounts show as needing a login; sign in again and carry on.

Once there is an account, runs happen every `interval-minutes` (six hours by default), and **Sync now** starts one at any time. `docker stop` cancels a run in progress cleanly: the book being downloaded goes back to the queue.

For a single sync-and-exit instead of the service:

```
docker compose run --rm audible-sync uv run --no-sync audible-sync
```

Add `--full` to make it a full refresh, re-reading every book's details from Audible (see `daily-full-refresh` in [Configuration](configuration.md#sync)).

## Running with Python (no Docker)

If you'd rather run the app directly in Python (e.g. for development or scripting), follow these steps:

### 1. Clone the Repository

```bash
git clone https://github.com/alexstansfield/audible-sync.git
cd audible-sync
```

### 2. Create and Activate a Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies

```
pip install -r requirements.txt
```

### 4. Install FFmpeg

This app requires [FFmpeg](https://ffmpeg.org/) to be installed and available on your system. For the `oga` output format it must be version 5 or newer and built with libopus, which the packages below all are.

#### macOS

```
brew install ffmpeg
```

#### Ubuntu/Debian

```
sudo apt install ffmpeg
```

#### Windows

1. Download the FFmpeg executable from [here](https://ffmpeg.org/download.html).
2. Extract the downloaded files.
3. Add the bin/ folder inside the extracted directory to your system's PATH.

### 5. Build the web app (optional)

Without it the service serves the API alone. With Node 22.12 or newer:

```
cd web && npm ci && npm run build && cd ..
```

### 6. Run

The service:

```
python -m audible_sync.service
```

or a single sync-and-exit, with `--full` a full refresh:

```
audible-sync [--full]
```

To add an account from here, see [Accounts](accounts.md#from-the-command-line). The service's environment variables are in [Configuration](configuration.md#environment-variables).

## Installing the web app

The web app can be installed on a phone or a computer, so it opens in a window of its own with its own icon: **Add to Home Screen** in Safari's share menu on an iPhone, **Install app** in Chrome's or Edge's menu. With the service down, an installed app still opens and says Cannot reach Audible Sync.

A browser offers this only over **https**, or at `http://localhost`. At a plain http address on a LAN, such as `http://192.168.1.10:8080`, the web app works as an ordinary page and nothing is installed. To install it, serve it over https from a reverse proxy, at the root of a domain or under a path (below), with `AUDIBLE_SYNC_SECURE_COOKIE=true`.

An installed app keeps the build it opened with. After an upgrade it says **Update available**, and **Reload** moves it to the new one. Closing every window of it does too. The service's state, your library and the settings are never kept on the device: they are read from the service every time.

## Behind a reverse proxy, under a path

To serve the app at `https://my.domain/audible-sync/` rather than at the root of a domain, set `AUDIBLE_SYNC_BASE_PATH=/audible-sync` and `AUDIBLE_SYNC_SECURE_COOKIE=true` (the proxy serves https; the service only sees http). The proxy may strip the prefix or pass it on; the service accepts either. The web app is the same build either way: the service tells it the path when it serves the page, and scopes the sign-in cookie to it. `/docs` is then at `/audible-sync/docs`. The path cannot start with one of the app's own (`/api`, `/assets`, `/docs`, `/library`, `/settings` and the other pages, or a file such as `/sw.js`); the service refuses to start with one. Installed under a path, the web app is a separate app from one at the root of the same domain.

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
