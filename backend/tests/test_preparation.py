import unittest
import tempfile
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import settings
from app.api import preparation as preparation_api
from app.main import app
from app.services.gemini import GeminiConfigurationError, GeminiGenerationError


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.database_dir = tempfile.TemporaryDirectory()
        old_db_path = settings.HISTORY_DB_PATH
        settings.HISTORY_DB_PATH = f"{self.database_dir.name}\\history.db"
        self.addCleanup(setattr, settings, "HISTORY_DB_PATH", old_db_path)
        self.addCleanup(self.database_dir.cleanup)
        self.client = TestClient(app)

    def test_generates_a_question_for_each_mode(self):
        modes = ("dsa", "sql", "hr")
        with patch.object(
            preparation_api,
            "generate_preparation_question",
            side_effect=("Explain binary search.", "Write a join query.", "Tell me about a challenge."),
        ) as generate:
            for mode, question in zip(
                modes,
                ("Explain binary search.", "Write a join query.", "Tell me about a challenge."),
            ):
                response = self.client.post(
                    "/api/preparation/question",
                    json={"mode": mode},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["mode"], mode)
                self.assertEqual(response.json()["question"], question)
                self.assertTrue(response.json()["session_id"])
                self.assertTrue(response.json()["question_id"])
        self.assertEqual(
            [call.args[0] for call in generate.call_args_list],
            list(modes),
        )

    def test_question_endpoint_logs_generation_history_and_total_timings(self):
        with (
            patch.object(
                preparation_api,
                "generate_preparation_question",
                return_value="Explain binary search.",
            ),
            self.assertLogs(preparation_api.logger, level="INFO") as captured,
        ):
            response = self.client.post(
                "/api/preparation/question",
                json={"mode": "dsa"},
            )

        self.assertEqual(response.status_code, 200)
        messages = "\n".join(captured.output)
        self.assertIn("Preparation question generation finished", messages)
        self.assertIn("Preparation session creation finished", messages)
        self.assertIn("Preparation question persistence finished", messages)
        self.assertIn("Preparation question endpoint finished", messages)

    def test_history_returns_404_when_the_backend_database_is_replaced(self):
        with patch.object(
            preparation_api,
            "generate_preparation_question",
            return_value="Explain binary search.",
        ):
            created = self.client.post(
                "/api/preparation/question",
                json={"mode": "dsa"},
            )
        self.assertEqual(created.status_code, 200)
        session_id = created.json()["session_id"]

        with tempfile.TemporaryDirectory() as replacement_database:
            with patch.object(
                settings,
                "HISTORY_DB_PATH",
                f"{replacement_database}\\history.db",
            ):
                history = self.client.get(
                    f"/api/preparation/history/{session_id}"
                )

        self.assertEqual(history.status_code, 404)
        self.assertEqual(history.json()["detail"], "Practice session not found.")

    def test_evaluates_all_modes_with_structured_feedback(self):
        modes = ("dsa", "sql", "hr")
        feedback = {
            "score": 85,
            "strengths": ["Clear reasoning"],
            "improvements": ["Explain edge cases"],
            "suggested_answer": "A model explanation.",
        }
        with patch.object(
            preparation_api, "evaluate_preparation_answer", return_value=feedback
        ) as evaluate:
            for mode in modes:
                response = self.client.post(
                    "/api/preparation/evaluate",
                    json={
                        "mode": mode,
                        "question": "Explain your approach?",
                        "answer": "I would start with the requirements.",
                    },
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["mode"], mode)
                for key, value in feedback.items():
                    self.assertEqual(response.json()[key], value)
                self.assertTrue(response.json()["session_id"])
                self.assertTrue(response.json()["question_id"])
                self.assertTrue(response.json()["answer_id"])
                self.assertTrue(response.json()["evaluation_id"])
        self.assertEqual(evaluate.call_count, 3)
        self.assertEqual(
            [call.args[0] for call in evaluate.call_args_list],
            list(modes),
        )

    def test_rejects_invalid_modes_and_blank_inputs(self):
        invalid_mode = self.client.post(
            "/api/preparation/question", json={"mode": "python"}
        )
        blank_topic = self.client.post(
            "/api/preparation/question", json={"mode": "dsa", "topic": " "}
        )
        blank_answer = self.client.post(
            "/api/preparation/evaluate",
            json={"mode": "hr", "question": "Question?", "answer": "  "},
        )
        blank_question = self.client.post(
            "/api/preparation/evaluate",
            json={"mode": "sql", "question": " ", "answer": "SELECT 1"},
        )

        self.assertEqual(invalid_mode.status_code, 422)
        self.assertEqual(blank_topic.status_code, 422)
        self.assertEqual(blank_answer.status_code, 422)
        self.assertEqual(blank_question.status_code, 422)

    def test_gemini_configuration_error_returns_503(self):
        with patch.object(
            preparation_api,
            "generate_preparation_question",
            side_effect=GeminiConfigurationError("Gemini API key is not configured."),
        ):
            response = self.client.post(
                "/api/preparation/question", json={"mode": "dsa"}
            )
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("secret", response.text.lower())

    def test_gemini_generation_error_returns_502_for_both_routes(self):
        with patch.object(
            preparation_api,
            "generate_preparation_question",
            side_effect=GeminiGenerationError("Gemini could not generate a response."),
        ):
            question_response = self.client.post(
                "/api/preparation/question", json={"mode": "sql"}
            )
        with patch.object(
            preparation_api,
            "evaluate_preparation_answer",
            side_effect=GeminiGenerationError("Gemini could not generate a response."),
        ):
            evaluation_response = self.client.post(
                "/api/preparation/evaluate",
                json={"mode": "hr", "question": "Question?", "answer": "Answer."},
            )
        self.assertEqual(question_response.status_code, 502)
        self.assertEqual(evaluation_response.status_code, 502)

    def test_invalid_gemini_feedback_returns_502(self):
        with patch.object(
            preparation_api,
            "evaluate_preparation_answer",
            return_value={"score": 101},
        ):
            response = self.client.post(
                "/api/preparation/evaluate",
                json={"mode": "dsa", "question": "Question?", "answer": "Answer."},
            )
        self.assertEqual(response.status_code, 502)


if __name__ == "__main__":
    unittest.main()
