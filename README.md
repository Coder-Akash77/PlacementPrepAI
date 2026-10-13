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
