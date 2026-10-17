import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from google.genai.errors import APIError

from app.api import chat as chat_api
from app.main import app
from app.services import gemini
from app.services.gemini import GeminiConfigurationError, GeminiGenerationError


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.resume_id = str(uuid4())

    def test_chat_passes_retrieved_chunks_and_returns_answer(self):
        chunks = [{"section": "Skills", "text": "Python"}]
        with (
            patch.object(chat_api, "search_resume", return_value=chunks) as search,
            patch.object(
                chat_api, "answer_resume_question", return_value="You know Python."
            ) as answer,
        ):
            response = self.client.post(
                "/api/chat",
                json={"resume_id": self.resume_id, "question": "What skills do I have?"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"resume_id": self.resume_id, "answer": "You know Python."},
        )
        search.assert_called_once_with(self.resume_id, "What skills do I have?")
        answer.assert_called_once_with("What skills do I have?", chunks)

    def test_chat_rejects_blank_question(self):
        response = self.client.post(
            "/api/chat",
            json={"resume_id": self.resume_id, "question": "  "},
        )
        self.assertEqual(response.status_code, 422)

    def test_chat_reports_missing_resume(self):
        with patch.object(
            chat_api, "search_resume", side_effect=chat_api.ResumeIndexNotFound()
        ):
            response = self.client.post(
                "/api/chat",
                json={"resume_id": self.resume_id, "question": "What skills?"},
            )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Resume not found.")

    def test_chat_reports_missing_gemini_key_without_calling_model(self):
        with (
            patch.object(chat_api, "search_resume", return_value=[]),
            patch.object(
                chat_api,
                "answer_resume_question",
                side_effect=GeminiConfigurationError("Gemini API key is not configured."),
            ) as answer,
        ):
            response = self.client.post(
                "/api/chat",
                json={"resume_id": self.resume_id, "question": "What skills?"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertNotIn("key", response.text.lower().replace("api key is not configured.", ""))
        answer.assert_called_once()

    def test_chat_maps_gemini_errors_to_bad_gateway(self):
        with (
            patch.object(chat_api, "search_resume", return_value=[]),
            patch.object(
                chat_api,
                "answer_resume_question",
                side_effect=GeminiGenerationError("Gemini could not generate a response."),
            ),
        ):
            response = self.client.post(
                "/api/chat",
                json={"resume_id": self.resume_id, "question": "What skills?"},
            )
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("secret", response.text.lower())

    def test_gemini_service_uses_configured_client_and_resume_context(self):
        class FakeResponse:
            text = " Based on your resume, Python. "

        class FakeModels:
            def generate_content(self, **kwargs):
                self.kwargs = kwargs
                return FakeResponse()

        class FakeClient:
            def __init__(self, api_key):
                self.api_key = api_key
                self.models = FakeModels()

        with (
            patch.object(gemini.settings, "GEMINI_API_KEY", "test-secret"),
            patch.object(gemini.settings, "GEMINI_MODEL", "test-model"),
            patch.object(gemini.genai, "Client", FakeClient),
        ):
            answer = gemini.answer_resume_question(
                "Which skill?",
                [{"section": "Skills", "text": "Python"}],
            )

        self.assertEqual(answer, "Based on your resume, Python.")

    def test_gemini_service_handles_missing_api_key(self):
        with (
            patch.object(gemini.settings, "GEMINI_API_KEY", ""),
            patch.object(gemini.genai, "Client") as client,
        ):
            with self.assertRaises(GeminiConfigurationError):
                gemini.answer_resume_question("Question?", [])
        client.assert_not_called()

    def test_gemini_service_wraps_api_error_without_exposing_details(self):
        class FakeModels:
            def generate_content(self, **kwargs):
                raise APIError(500, {"message": "sensitive provider detail"})

        class FakeClient:
            def __init__(self, api_key):
                self.models = FakeModels()

        with (
            patch.object(gemini.settings, "GEMINI_API_KEY", "test-secret"),
            patch.object(gemini.genai, "Client", FakeClient),
        ):
            with self.assertRaises(GeminiGenerationError) as error:
                gemini.answer_resume_question("Question?", [])
        self.assertNotIn("sensitive", str(error.exception))


if __name__ == "__main__":
    unittest.main()
