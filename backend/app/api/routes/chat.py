from fastapi import APIRouter
from pydantic import BaseModel

from app.services.embedding_service import model
from app.services.vector_db_service import search_similar_chunks
from app.services.llm_service import ask_gemini


router = APIRouter(
    prefix="/chat",
    tags=["Chat"]
)


class ChatRequest(BaseModel):
    question: str


def detect_section(question):

    question = question.lower()

    if any(word in question for word in ["project", "projects"]):
        return "Projects"

    if any(word in question for word in ["skill", "skills", "technology", "technologies"]):
        return "Skills"

    if any(word in question for word in ["education", "degree", "college", "university"]):
        return "Education"

    if any(word in question for word in ["experience", "job", "work"]):
        return "Experience"

    return None


@router.post("/")
def chat(request: ChatRequest):

    query_embedding = model.encode(request.question)

    section = detect_section(request.question)

    chunks = search_similar_chunks(
        query_embedding,
        n_results=10,
        section=section
    )

    context = "\n\n".join(chunks)

    answer = ask_gemini(
        context,
        request.question
    )

    return {
        "answer": answer
    }