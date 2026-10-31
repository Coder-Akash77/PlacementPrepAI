import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from google.genai.errors import ClientError

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
            def __init__(self, api_key, http_options=None):
                self.api_key = api_key
                self.http_options = http_options
                self.models = FakeModels()

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

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
                raise ClientError(
                    404,
                    {
                        "error": {
                            "code": 404,
                            "status": "NOT_FOUND",
                            "message": "Model unavailable; api_key=test-secret",
                        }
                    },
                )

        class FakeClient:
            def __init__(self, api_key, http_options=None):
                self.models = FakeModels()

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

        with (
            patch.object(gemini.settings, "GEMINI_API_KEY", "test-secret"),
            patch.object(gemini.genai, "Client", FakeClient),
            self.assertLogs(gemini.logger, level="WARNING") as captured,
        ):
            with self.assertRaises(GeminiGenerationError) as error:
                gemini.answer_resume_question("Question?", [])
        self.assertNotIn("sensitive", str(error.exception))
        self.assertIn("404", captured.output[0])
        self.assertIn("NOT_FOUND", captured.output[0])
        self.assertIn("error_type=ClientError", captured.output[0])
        self.assertIn("status_code=404", captured.output[0])
        self.assertNotIn("Model unavailable", captured.output[0])
        self.assertNotIn("test-secret", captured.output[0])

    def test_gemini_sdk_retries_transient_503_then_succeeds(self):
        requests = []

        def respond(request):
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(
                    503,
                    json={
                        "error": {
                            "code": 503,
                            "status": "UNAVAILABLE",
                            "message": "Temporarily busy.",
                        }
                    },
                    request=request,
                )
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {"content": {"parts": [{"text": "A practice question."}]}}
                    ]
                },
                request=request,
            )

        options = gemini._gemini_http_options()
        options.client_args = {"transport": httpx.MockTransport(respond)}
        with (
            patch.object(gemini.settings, "GEMINI_API_KEY", "test-secret"),
            patch.object(gemini.settings, "GEMINI_MODEL", "test-model"),
            patch.object(gemini, "_gemini_http_options", return_value=options),
            self.assertLogs(gemini.logger, level="INFO") as captured,
        ):
            result = gemini.generate_preparation_question("dsa")

        self.assertEqual(result, "A practice question.")
        self.assertEqual(len(requests), 2)
        self.assertEqual(options.retry_options.http_status_codes, [503])
        self.assertEqual(options.retry_options.attempts, 3)
        messages = "\n".join(captured.output)
        self.assertEqual(messages.count("Gemini HTTP attempt started"), 2)
        self.assertIn("status_code=503", messages)
        self.assertIn("status_code=200", messages)
        self.assertIn("duration_ms=", messages)
        self.assertIn("Gemini generate_content call finished", messages)

    def test_gemini_http_options_use_20_second_timeout_and_503_retries(self):
        options = gemini._gemini_http_options()

        self.assertEqual(options.timeout, 20_000)
        self.assertEqual(options.retry_options.attempts, 3)
        self.assertEqual(options.retry_options.http_status_codes, [503])

    def test_gemini_sdk_timeout_maps_to_generation_error(self):
        requests = []

        def respond(request):
            requests.append(request)
            raise httpx.ReadTimeout("request failed with test-secret")

        options = gemini._gemini_http_options()
        options.client_args = {"transport": httpx.MockTransport(respond)}
        with (
            patch.object(gemini.settings, "GEMINI_API_KEY", "test-secret"),
            patch.object(gemini.settings, "GEMINI_MODEL", "test-model"),
            patch.object(gemini, "_gemini_http_options", return_value=options),
            self.assertLogs(gemini.logger, level="WARNING") as captured,
        ):
            with self.assertRaises(GeminiGenerationError) as error:
                gemini.generate_preparation_question("dsa")

        self.assertEqual(str(error.exception), "Gemini request timed out.")
        self.assertNotIn("test-secret", "\n".join(captured.output))
        self.assertIn("Gemini request timed out.", "\n".join(captured.output))
        self.assertIn("error_type=ReadTimeout", "\n".join(captured.output))
        self.assertIn("status_code=unknown", "\n".join(captured.output))
        self.assertEqual(len(requests), 3)

    def test_gemini_resume_timeout_maps_to_generation_error(self):
        with (
            patch.object(gemini.settings, "GEMINI_API_KEY", "test-secret"),
            patch.object(
                gemini.genai,
                "Client",
                side_effect=httpx.ReadTimeout("request failed with test-secret"),
            ),
            self.assertLogs(gemini.logger, level="WARNING") as captured,
        ):
            with self.assertRaises(GeminiGenerationError) as error:
                gemini.answer_resume_question("Question?", [])

        self.assertEqual(str(error.exception), "Gemini request timed out.")
        self.assertNotIn("test-secret", "\n".join(captured.output))

    def test_gemini_sdk_503_retries_exhausted_map_to_generation_error(self):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(
                503,
                json={
                    "error": {
                        "code": 503,
                        "status": "UNAVAILABLE",
                        "message": "Temporarily busy.",
                    }
                },
                request=request,
            )

        options = gemini._gemini_http_options()
        options.client_args = {"transport": httpx.MockTransport(respond)}
        with (
            patch.object(gemini.settings, "GEMINI_API_KEY", "test-secret"),
            patch.object(gemini.settings, "GEMINI_MODEL", "test-model"),
            patch.object(gemini, "_gemini_http_options", return_value=options),
            self.assertLogs(gemini.logger, level="WARNING") as captured,
        ):
            with self.assertRaises(GeminiGenerationError):
                gemini.generate_preparation_question("sql")

        self.assertEqual(len(requests), 3)
        messages = "\n".join(captured.output)
        self.assertIn("error_type=ServerError", messages)
        self.assertIn("status_code=503", messages)
        self.assertIn("status=UNAVAILABLE", messages)

    def test_gemini_sdk_does_not_retry_authentication_errors(self):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(
                401,
                json={
                    "error": {
                        "code": 401,
                        "status": "UNAUTHENTICATED",
                        "message": "Invalid credentials.",
                    }
                },
                request=request,
            )

        options = gemini._gemini_http_options()
        options.client_args = {"transport": httpx.MockTransport(respond)}
        with (
            patch.object(gemini.settings, "GEMINI_API_KEY", "test-secret"),
            patch.object(gemini.settings, "GEMINI_MODEL", "test-model"),
            patch.object(gemini, "_gemini_http_options", return_value=options),
            self.assertLogs(gemini.logger, level="WARNING"),
        ):
            with self.assertRaises(GeminiGenerationError):
                gemini.generate_preparation_question("hr")

        self.assertEqual(len(requests), 1)


if __name__ == "__main__":
    unittest.main()
