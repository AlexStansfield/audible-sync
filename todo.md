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
- [ ] Audible Auth Json path
- [ ] Add Docker Compose file to run container
- [ ] Add github action to build image and push to repo

## Milestone 2: Download Progress, Metadata and OGA Encoding

### Requirements

- Download Progress
- Improved Metadata generation to include cover image and asin
- Fetch Cover, PDF and Annotations data
- Move settings to database
- Encode books to OGA 
- Add encoding settings

### Tasks

- [ ] Add logging
- [ ] Aysnc downloader to monitor progress of download
- [ ] Download PDF
- [ ] Download Cover
- [ ] Download Annotations
- [ ] Generate metadata
- [ ] Add metadata to m4b
- [ ] Add settings for encoding bitrate
- [ ] Encode books to OGA instead of M4B if configured
- [ ] Name m4b/oga files based on metadata

## Milestone 4: API Service

### Requirements

- Login to audible
- Convert to an API service
- Get library of books by API
- Get and Change Settings by API

### Tasks

- [ ] Add settings table to database
- [ ] Initialise settings on initial run
- [ ] Convert to an API service
- [ ] Login and save credentials
