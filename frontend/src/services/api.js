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
