# Todo

List of tasks to complete by phases

## Milestone 1: Library Sync, Download and Decrypt

### Requirements

- Initial Sync of library from audible
- Record sync status for each book
- Download books
- Decrypt books to m4b
- Settings: Number of books to sync, destination folder
- Docker Support

### Tasks

- [x] Create Database
- [x] Create initial Book Model
- [x] Create Audible model
- [x] Book download
- [x] Settings file
- [x] Check for initial sync and get all books
- [x] Fetch library since last sync
- [x] Sync configured number of books
- [x] Decrypt book to m4b
- [x] Add DockerFile to build a container with requirements
- [x] Create directory for each download
- [x] Move decrypted file to folder
- [x] Cleanup files after finished
- [x] Audible Auth Json path
- [x] Add Docker Compose file to run container
- [x] Add github action to build image and push to repo

## Milestone 2: Download Progress, Metadata and OGA Encoding

### Requirements

- Download Progress
- Improved Metadata generation to include cover image and asin
- Fetch Cover, PDF and Annotations data
- Move settings to database
- Encode books to OGA 
- Add encoding settings

### Tasks

- [x] Add logging
- [x] Download PDF
- [x] Download Cover
- [x] Download Annotations
- [x] Generate metadata
- [x] Add metadata to m4b
- [x] Add settings for encoding bitrate
- [x] Encode books to OGA instead of M4B if configured
- [x] Name m4b/oga files based on metadata

## Milestone 3: API Service

### Requirements

- Support Multiple Audible Subscriptions
- Login to audible via website
- Convert to an API service
- API must be designed with a frontend in mind (milestone 4)
- Get library of books by API
- Get and Change Settings by API
- Monitor Download progress
- Ability to "delete" a download or force a redownload

### Tasks

- [x] Add settings table to database
- [x] Initialise settings on initial run
- [x] Convert to an API service: background scheduler, status, manual sync trigger and cancel, run history and settings endpoints, bearer token, Docker image runs the service
- [x] Monitor download progress (the run in flight is polled through `GET /api/status`)
- [x] Add support for multiple audible subscriptions: one account per marketplace login, credentials in the database, per-account library, cursor and run history, account endpoints
- [x] Login and save credentials: browser sign-in split into start/complete API calls (and a CLI `login` command), credentials stored on the account
- [x] Delete a book: `POST /api/books/{id}/delete-files` removes the files and unmonitors; `/redownload` and `/retry` queue it again
- [x] Set book as monitored/unmonitored to remove from sync (`PATCH /api/books/{id}`), plus a paged, searchable `GET /api/books` and a cover endpoint
- [x] Extras: `GET /api/logs`, a webhook posted after every run (`webhook-url`, with `POST /api/settings/webhook/test`), `GET /api/stats`

## Milestone 4: Web App Frontend

A web application that exposes a UI to the API allowing you to login to audible, view your library, manage your sync settings and sync books

### Requirements

- Login to Audible Sync
- Authenticate with Audible 
- Configure settings
- View Library
- View Book Details
- Delete synced book
- Toggle Monitor of book

### Tasks

To be defined

