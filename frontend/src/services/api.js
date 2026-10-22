const API_URL = (
  import.meta.env.VITE_API_URL || "http://localhost:8000/api"
).replace(/\/+$/, "");
async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(`${API_URL}${path}`, options);
  } catch {
    throw new Error("Could not reach the backend. Check that it is running.");
  }

  let payload;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (!response.ok) {
    const detail = payload?.detail;
    const message = Array.isArray(detail)
      ? detail.map((item) => item.msg).join(" ")
      : detail;
    throw new Error(
      message || `Request failed with status ${response.status}.`
    );
  }

  return payload;
}

export function checkHealth() {
  return request("/health");
}

export function uploadResume(file) {
  const formData = new FormData();
  formData.append("file", file);
  return request("/resume/upload", {
    method: "POST",
    body: formData,
  });
}

export function sendChatMessage(resumeId, question) {
  return request("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ resume_id: resumeId, question }),
  });
}

export function startInterview(resumeId) {
  return request("/interview/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ resume_id: resumeId }),
  });
}

export function submitInterviewAnswer(sessionId, answer) {
  return request(`/interview/${encodeURIComponent(sessionId)}/answer`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ answer }),
  });
}

export function requestNextQuestion(sessionId) {
  return request(`/interview/${encodeURIComponent(sessionId)}/next`, {
    method: "POST",
  });
}

export function evaluateInterviewAnswer(sessionId) {
  return request(`/interview/${encodeURIComponent(sessionId)}/evaluate`, {
    method: "POST",
  });
}

export function generatePreparationQuestion(mode, topic, sessionId) {
  return request("/preparation/question", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      mode,
      ...(topic ? { topic } : {}),
      ...(sessionId ? { session_id: sessionId } : {}),
    }),
  });
}

export function evaluatePreparationAnswer(
  mode,
  question,
  answer,
  sessionId,
  questionId
) {
  return request("/preparation/evaluate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      mode,
      question,
      answer,
      ...(sessionId ? { session_id: sessionId } : {}),
      ...(questionId ? { question_id: questionId } : {}),
    }),
  });
}

export function getPreparationHistory(sessionId) {
  return request(`/preparation/history/${encodeURIComponent(sessionId)}`);
}

export function getPreparationQuestionHistory(questionId) {
  return request(
    `/preparation/history/questions/${encodeURIComponent(questionId)}`
  );
}

export function submitHistoricalPreparationAnswer(questionId, answer) {
  return request(
    `/preparation/history/questions/${encodeURIComponent(questionId)}/answers`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answer }),
    }
  );
}

export function reevaluateHistoricalPreparationAnswer(answerId) {
  return request(
    `/preparation/history/answers/${encodeURIComponent(answerId)}/evaluate`,
    { method: "POST" }
  );
}

export function getInterviewHistory(resumeId) {
  return request(`/interview/history?resume_id=${encodeURIComponent(resumeId)}`);
}

export function getInterviewHistorySession(sessionId) {
  return request(`/interview/history/${encodeURIComponent(sessionId)}`);
}

export function getInterviewQuestionHistory(questionId) {
  return request(
    `/interview/history/questions/${encodeURIComponent(questionId)}`
  );
}

export function submitHistoricalInterviewAnswer(questionId, answer) {
  return request(
    `/interview/history/questions/${encodeURIComponent(questionId)}/answers`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answer }),
    }
  );
}

export function reevaluateHistoricalInterviewAnswer(answerId) {
  return request(
    `/interview/history/answers/${encodeURIComponent(answerId)}/evaluate`,
    { method: "POST" }
  );
}
