import unittest
import tempfile
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api import interview as interview_api
from app.main import app
from app.config import settings
from app.services import interview as interview_service
from app.services.gemini import GeminiGenerationError
from app.services.interview import _sessions, _sessions_lock


class InterviewTests(unittest.TestCase):
    def setUp(self):
        with _sessions_lock:
            _sessions.clear()
        self.database_dir = tempfile.TemporaryDirectory()
        old_db_path = settings.HISTORY_DB_PATH
        settings.HISTORY_DB_PATH = f"{self.database_dir.name}\\history.db"
        self.addCleanup(setattr, settings, "HISTORY_DB_PATH", old_db_path)
        self.addCleanup(self.database_dir.cleanup)
        self.client = TestClient(app)
        self.resume_id = str(uuid4())
        self.chunks = [{"section": "Experience", "text": "Built APIs"}]
        self.patches = [
            patch.object(
                interview_api, "search_resume", return_value=self.chunks
            ),
            patch.object(
                interview_api,
                "generate_interview_question",
                side_effect=["Describe an API you built.", "How did you test it?"],
            ),
            patch.object(
                interview_api,
                "evaluate_interview_answer",
                return_value={
                    "score": 82,
                    "strengths": ["Clear explanation"],
                    "improvements": ["Add measurable impact"],
                    "improved_sample_answer": "I built an API that ...",
                },
            ),
        ]
        self.search, self.generate, self.evaluate = [
            mocked.start() for mocked in self.patches
        ]
        for mocked in self.patches:
            self.addCleanup(mocked.stop)

    def start(self):
        return self.client.post(
            "/api/interview/start", json={"resume_id": self.resume_id}
        )

    def test_interview_flow_evaluates_only_when_requested(self):
        started = self.start()
        self.assertEqual(started.status_code, 200)
        session_id = started.json()["session_id"]
        self.assertEqual(started.json()["question"], "Describe an API you built.")
        self.evaluate.assert_not_called()

        submitted = self.client.post(
            f"/api/interview/{session_id}/answer",
            json={"answer": "I built a REST API."},
        )
        self.assertEqual(submitted.status_code, 200)
        self.assertTrue(submitted.json()["answer_recorded"])
        self.evaluate.assert_not_called()

        evaluation = self.client.post(f"/api/interview/{session_id}/evaluate")
        self.assertEqual(evaluation.status_code, 200)
        body = evaluation.json()
        self.assertEqual(body["score"], 82)
        self.assertEqual(body["strengths"], ["Clear explanation"])
        self.assertEqual(body["improvements"], ["Add measurable impact"])
        self.assertEqual(body["improved_sample_answer"], "I built an API that ...")
        self.evaluate.assert_called_once_with(
            "Describe an API you built.", "I built a REST API.", self.chunks
        )

        next_question = self.client.post(f"/api/interview/{session_id}/next")
        self.assertEqual(next_question.status_code, 200)
        self.assertEqual(next_question.json()["question"], "How did you test it?")
        self.assertEqual(self.generate.call_count, 2)
        self.assertEqual(
            self.generate.call_args.args[1],
            ["Describe an API you built."],
        )

    def test_sessions_are_isolated_by_session_id(self):
        first = self.start().json()
        second = self.start().json()
        self.assertNotEqual(first["session_id"], second["session_id"])

        response = self.client.post(
            f"/api/interview/{first['session_id']}/answer",
            json={"answer": "First candidate answer"},
        )
        self.assertEqual(response.status_code, 200)

        first_session = interview_service.get_session(first["session_id"])
        second_session = interview_service.get_session(second["session_id"])
        self.assertTrue(first_session.answer_submitted)
        self.assertFalse(second_session.answer_submitted)
        self.assertEqual(second_session.history, [])

    def test_next_and_evaluate_require_an_answer(self):
        session_id = self.start().json()["session_id"]
        next_response = self.client.post(f"/api/interview/{session_id}/next")
        evaluation_response = self.client.post(
            f"/api/interview/{session_id}/evaluate"
        )
        self.assertEqual(next_response.status_code, 409)
        self.assertEqual(evaluation_response.status_code, 409)
        self.generate.assert_called_once()
        self.evaluate.assert_not_called()

    def test_rejects_blank_and_duplicate_answers(self):
        session_id = self.start().json()["session_id"]
        blank = self.client.post(
            f"/api/interview/{session_id}/answer", json={"answer": "   "}
        )
        self.assertEqual(blank.status_code, 422)

        first = self.client.post(
            f"/api/interview/{session_id}/answer", json={"answer": "An answer"}
        )
        duplicate = self.client.post(
            f"/api/interview/{session_id}/answer", json={"answer": "Another answer"}
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(duplicate.status_code, 409)
        self.evaluate.assert_not_called()

    def test_unknown_session_returns_404(self):
        missing_id = str(uuid4())
        for action in ("answer", "next", "evaluate"):
            response = self.client.post(
                f"/api/interview/{missing_id}/{action}",
                json={"answer": "Answer"} if action == "answer" else None,
            )
            self.assertEqual(response.status_code, 404)

    def test_start_returns_404_for_missing_resume(self):
        with patch.object(
            interview_api,
            "search_resume",
            side_effect=interview_api.ResumeIndexNotFound(),
        ):
            response = self.start()
        self.assertEqual(response.status_code, 404)
        self.generate.assert_not_called()

    def test_invalid_gemini_evaluation_returns_502(self):
        session_id = self.start().json()["session_id"]
        self.client.post(
            f"/api/interview/{session_id}/answer",
            json={"answer": "An answer"},
        )
        with patch.object(
            interview_api,
            "evaluate_interview_answer",
            return_value={"score": 101},
        ):
            response = self.client.post(
                f"/api/interview/{session_id}/evaluate"
            )
        self.assertEqual(response.status_code, 502)

    def test_gemini_generation_failure_returns_502(self):
        with patch.object(
            interview_api,
            "generate_interview_question",
            side_effect=GeminiGenerationError("Generation failed."),
        ):
            response = self.start()
        self.assertEqual(response.status_code, 502)


if __name__ == "__main__":
    unittest.main()
