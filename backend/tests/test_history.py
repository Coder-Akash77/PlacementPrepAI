import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api import interview as interview_api, preparation as preparation_api
from app.config import settings
from app.main import app
from app.services import history
from app.services.interview import _sessions, _sessions_lock


class PersistentHistoryTests(unittest.TestCase):
    def setUp(self):
        self.database_dir = tempfile.TemporaryDirectory()
        self.old_db_path = settings.HISTORY_DB_PATH
        settings.HISTORY_DB_PATH = f"{self.database_dir.name}\\history.db"
        self.addCleanup(setattr, settings, "HISTORY_DB_PATH", self.old_db_path)
        self.addCleanup(self.database_dir.cleanup)
        with _sessions_lock:
            _sessions.clear()
        self.client = TestClient(app)
        self.resume_id = str(uuid4())
        self.feedback = {
            "score": 80,
            "strengths": ["Clear response"],
            "improvements": ["Add detail"],
            "improved_sample_answer": "A stronger response.",
        }

    def test_interview_history_survives_memory_reset_and_keeps_attempts(self):
        with (
            patch.object(interview_api, "search_resume", return_value=[]),
            patch.object(
                interview_api,
                "generate_interview_question",
                return_value="Describe a project.",
            ),
            patch.object(
                interview_api, "evaluate_interview_answer", return_value=self.feedback
            ),
        ):
            started = self.client.post(
                "/api/interview/start", json={"resume_id": self.resume_id}
            ).json()
            session_id = started["session_id"]
            question_id = started["question_id"]
            self.client.post(
                f"/api/interview/{session_id}/answer",
                json={"answer": "First answer."},
            )
            evaluated = self.client.post(
                f"/api/interview/{session_id}/evaluate"
            )
            self.assertEqual(evaluated.status_code, 200)
            first_answer_id = history.get_interview_question(question_id)["answers"][0][
                "answer_id"
            ]

            with _sessions_lock:
                _sessions.clear()
            session_detail = self.client.get(
                f"/api/interview/history/{session_id}"
            )
            self.assertEqual(session_detail.status_code, 200)
            self.assertEqual(session_detail.json()["questions"][0]["question_id"], question_id)
            self.assertEqual(
                self.client.get(
                    f"/api/interview/history?resume_id={self.resume_id}"
                ).json()["sessions"][0]["session_id"],
                session_id,
            )

            second_answer = self.client.post(
                f"/api/interview/history/questions/{question_id}/answers",
                json={"answer": "Second answer."},
            ).json()
            self.assertNotEqual(first_answer_id, second_answer["answer_id"])
            reevaluated = self.client.post(
                f"/api/interview/history/answers/{first_answer_id}/evaluate"
            )
            self.assertEqual(reevaluated.status_code, 200)
            detail = self.client.get(
                f"/api/interview/history/questions/{question_id}"
            ).json()
            self.assertEqual(len(detail["answers"]), 2)
            self.assertEqual(len(detail["answers"][0]["evaluations"]), 2)
            self.assertEqual(detail["answers"][1]["answer"], "Second answer.")

    def test_preparation_history_preserves_answers_and_evaluations(self):
        feedback = {
            "score": 75,
            "strengths": ["Good approach"],
            "improvements": ["Discuss complexity"],
            "suggested_answer": "A model answer.",
        }
        with (
            patch.object(
                preparation_api,
                "generate_preparation_question",
                return_value="Explain binary search.",
            ),
            patch.object(
                preparation_api, "evaluate_preparation_answer", return_value=feedback
            ) as evaluate,
        ):
            question = self.client.post(
                "/api/preparation/question", json={"mode": "dsa"}
            ).json()
            result = self.client.post(
                "/api/preparation/evaluate",
                json={
                    "mode": "dsa",
                    "question": question["question"],
                    "answer": "Compare the middle value.",
                    "session_id": question["session_id"],
                    "question_id": question["question_id"],
                },
            )
            self.assertEqual(result.status_code, 200)
            answer_id = result.json()["answer_id"]
            second_answer = self.client.post(
                f"/api/preparation/history/questions/{question['question_id']}/answers",
                json={"answer": "A second attempt."},
            )
            self.assertEqual(second_answer.status_code, 200)
            reevaluation = self.client.post(
                f"/api/preparation/history/answers/{answer_id}/evaluate"
            )
            self.assertEqual(reevaluation.status_code, 200)
            self.assertEqual(evaluate.call_count, 2)

            detail = self.client.get(
                f"/api/preparation/history/questions/{question['question_id']}"
            ).json()
            self.assertEqual(len(detail["answers"]), 2)
            self.assertEqual(len(detail["answers"][0]["evaluations"]), 2)
            self.assertEqual(detail["answers"][1]["answer"], "A second attempt.")
            session = self.client.get(
                f"/api/preparation/history/{question['session_id']}"
            ).json()
            self.assertEqual(session["questions"][0]["question_id"], question["question_id"])

    def test_history_missing_ids_and_storage_errors_are_safe(self):
        missing = str(uuid4())
        interview_missing = self.client.get(
            f"/api/interview/history/{missing}"
        )
        preparation_missing = self.client.get(
            f"/api/preparation/history/{missing}"
        )
        invalid_answer = self.client.post(
            f"/api/preparation/history/questions/{missing}/answers",
            json={"answer": "  "},
        )
        self.assertEqual(interview_missing.status_code, 404)
        self.assertEqual(preparation_missing.status_code, 404)
        self.assertEqual(invalid_answer.status_code, 422)

        with patch.object(
            preparation_api.history,
            "get_preparation_session",
            side_effect=history.HistoryStorageError("details must stay hidden"),
        ):
            failed = self.client.get(
                f"/api/preparation/history/{str(uuid4())}"
            )
        self.assertEqual(failed.status_code, 503)
        self.assertNotIn("details must stay hidden", failed.text)


if __name__ == "__main__":
    unittest.main()
