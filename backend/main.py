from fastapi import FastAPI
from app.api.routes.upload import router as upload_router
from app.api.routes.home import router as home_router
from app.api.routes.chat import router as chat_router


app = FastAPI(
    title="PlacementPrep AI",
    version="1.0.0"
)

app.include_router(home_router)
app.include_router(upload_router)
app.include_router(chat_router)