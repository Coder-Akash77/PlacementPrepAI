# PlacementPrepAI

Resume-aware AI placement preparation platform.

**Flow:** React → FastAPI → Embeddings → ChromaDB → relevant resume chunks → Gemini → response → React

## Stack
- Frontend: React + Vite, plain CSS
- Backend: Python + FastAPI
- Vector DB: ChromaDB
- Embeddings: sentence-transformers
- LLM: Gemini API

## Run the backend

```bash
cd backend
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # then add your GEMINI_API_KEY
uvicorn app.main:app --reload --port 8000
```

Check: http://localhost:8000/api/health and http://localhost:8000/docs

## Run the frontend

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

Open: http://localhost:5173

## Deploy on Render

Deploy both services from the repository's `render.yaml`:

1. In Render, choose **New > Blueprint** and connect this repository.
2. Review and deploy the Blueprint. It creates the FastAPI web service and the Vite static site.
3. In the backend service's **Environment** settings, set `GEMINI_API_KEY` to your Gemini key and `FRONTEND_ORIGINS` to the frontend's assigned origin (the URL without a path). Keep the key in Render; do not add it to the repository.
4. In the frontend static site's **Environment** settings, set `VITE_API_URL` to the backend's assigned URL followed by `/api` (for example, append `/api` to the URL Render shows for the backend). Save the change and trigger a new frontend deploy/build.

The Blueprint marks these values as dashboard-provided. Render may ask for them during the initial Blueprint setup; update the URL-dependent values in the service dashboards once Render assigns the service URLs. `VITE_API_URL` is embedded by Vite at build time, so changing it requires rebuilding the frontend.

The backend stores ChromaDB data, uploaded PDFs, and SQLite history under `/var/data` on its persistent disk. Persistent disks require a paid Render web-service plan and are attached to one service instance; keep the backend at a single instance so these local stores remain consistent. The services use the existing environment variable names and application routes.
