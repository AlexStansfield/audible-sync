# Todo

List of tasks to complete by phases

## Milestone 1: Initial Download

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
- [ ] Check for initial sync and get all books
- [ ] Decrypt book to m4b
- [ ] Sync configured number of books
- [ ] Add DockerFile to build a container with requirements
- [ ] Add Docker Compose file to run container

## Milestone 2: Sync new Books

### Requirements

- Sync new books to database
- Download Progress

### Tasks

- [ ] Add settings table to database
- [ ] Initialise settings on initial run
- [ ] Fetch library since last sync
- [ ] Aysnc downloader to monitor progress of download

## Milestone 3: Metadata and OGA Encoding

### Requirements

- Improved Metadata generation to include cover image and asin
- Fetch Cover, PDF and Annotations data
- Move settings to database
- Encode books to OGA 
- Add encoding settings

### Tasks

- [ ] Download PDF
- [ ] Download Cover
- [ ] Download Annotations
- [ ] Generate metadata
- [ ] Add metadata to m4b
- [ ] Add settings for encoding bitrate
- [ ] Encode books to OGA instead of M4B if configured

## Milestone 4: API Service

### Requirements

- Convert to an API service
- Get library of books by API
- Get and Change Settings by API

### Tasks

- [ ] Convert to an API service
