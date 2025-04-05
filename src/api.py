from fastapi import FastAPI
from src.sync import sync_library

app = FastAPI()

@app.get("/")
def home():
    return {"message": "Audible Sync API"}

@app.post("/sync")
def sync():
    result = sync_library()
    return {"status": "success", "books_synced": result}