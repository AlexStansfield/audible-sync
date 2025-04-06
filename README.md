# Audible Sync

A python app that will fetch your library from audible, download the books and convert them to DRM free M4B files.

On each run it will sync the latest purchases from your audible library to the app database.

It records the download status of each book so will only fetch the missing books

## Disclaimer

This app DOES NOT crack DRM. If that's what you're after, look elsewhere. 

What this app does do is decrypt the audiobooks that you have access to in your Audible library. It can only de-drm your books, no one elses.

The purpose of this app is to allow the user more control over how they consumer the books that they've paid for. I will never be adding any features to download books that are not in your library so please don't ask.

## Initial Setup

You will need to have an existing audible authentication json file. 

Later I plan to add support to be able to login and create this through the app but until then you need to create it by other means.

The easiest way is to use [audible-cli](https://github.com/mkb79/audible-cli). Take a look at the [Getting Started](https://github.com/mkb79/audible-cli?tab=readme-ov-file#getting-started) instruction on using the `quickstart` command. By default this should create a `.audible` folder in your home with an `audible.json` file inside. It's this file that is needed by this app.

If you decide not to store the file at the default location you can update the `audible-auth-file` setting in the `config.ini` to point to the file location.

## Config

The `config/config.ini` file has the following sections and options:

### `sync`

 - `max-download`: total number of books to download and decrypt on each app run, leave unset to get everything waiting to be downloaded
 - `audible-auth-file`: path to the audible auth json, leave unset to default to `$HOME/.audible/audible.json`

### `folders`

 - `downloads`: folder where files are temporarily downloaded to before being converted
 - `audiobooks`: folder to copy the converted audiobooks to

## Running

### Docker

The simplest of all, copy the `compose.yml` file from the repository to the location of your choice.

Update the volumne mounts to match where you want files to be stored.

If you want your own config then uncomment the volume mount line and place an updated copy of the `config.ini` file in the host side folder.

Then just run

```
docker compose run audible-sync
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

This app requires [FFmpeg](https://ffmpeg.org/) to be installed and available on your system.

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

## Todo

There is plenty left to do, my ultimate goal is to have a web based service that can automatically sync, download and convert your books. 

It will support:
- re-encoding the books to OGA format in order to reduce size
- fetching PDF files included with the book
- fetching extra metadata and cover art
- configurable folder and filename structure for converted books

The current todo list can be found [here](todo.md).