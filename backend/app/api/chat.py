from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.api.quota import enforce_user_quota
from app.services.gemini import (
    GeminiConfigurationError,
    GeminiGenerationError,
    GeminiTemporaryUnavailableError,
    answer_resume_question,
)
from app.services.resume import (
    ResumeEmbeddingConfigurationError,
    ResumeEmbeddingError,
    ResumeIndexIncompatible,
    ResumeIndexNotFound,
    search_resume,
)

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    resume_id: UUID
    question: str = Field(min_length=1, max_length=2000)

    @field_validator("question")
    @classmethod
    def question_must_not_be_blank(cls, question: str) -> str:
        question = question.strip()
        if not question:
            raise ValueError("Question must not be empty.")
        return question


@router.post("/chat", dependencies=[Depends(enforce_user_quota)])
def chat(request: ChatRequest):
    try:
        chunks = search_resume(str(request.resume_id), request.question)
    except ResumeIndexNotFound as exc:
        raise HTTPException(status_code=404, detail="Resume not found.") from exc
    except ResumeIndexIncompatible as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ResumeEmbeddingConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ResumeEmbeddingError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    try:
        answer = answer_resume_question(request.question, chunks)
    except GeminiConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except GeminiTemporaryUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except GeminiGenerationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {"resume_id": str(request.resume_id), "answer": answer}
