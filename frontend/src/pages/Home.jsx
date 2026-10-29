import { useEffect, useState } from "react";
import {
  checkHealth,
  evaluatePreparationAnswer,
  evaluateInterviewAnswer,
  getInterviewHistory,
  getInterviewHistorySession,
  getInterviewQuestionHistory,
  getPreparationHistory,
  getPreparationQuestionHistory,
  generatePreparationQuestion,
  reevaluateHistoricalInterviewAnswer,
  reevaluateHistoricalPreparationAnswer,
  requestNextQuestion,
  sendChatMessage,
  startInterview,
  submitHistoricalInterviewAnswer,
  submitHistoricalPreparationAnswer,
  submitInterviewAnswer,
  uploadResume,
} from "../services/api.js";

function ErrorMessage({ children }) {
  if (!children) return null;
  return (
    <p className="error-message" role="alert">
      {children}
    </p>
  );
}

function renderInlineMarkdown(text, keyPrefix) {
  return text.split(/(\*\*.+?\*\*|__.+?__)/g).map((part, index) => {
    const bold = part.match(/^(?:\*\*(.+)\*\*|__(.+)__)$/);
    return bold ? (
      <strong key={`${keyPrefix}-${index}`}>{bold[1] ?? bold[2]}</strong>
    ) : (
      <span key={`${keyPrefix}-${index}`}>{part}</span>
    );
  });
}

function MarkdownContent({ children }) {
  const lines = String(children)
    .split(/\r?\n/)
    .map((line) => line.replace(/\\+([*_#-])/g, "$1"));
  const blocks = [];
  let paragraph = [];
  let list = null;

  function flushParagraph() {
    if (!paragraph.length) return;
    blocks.push(
      <p className="markdown-paragraph" key={`paragraph-${blocks.length}`}>
        {paragraph.map((line, index) => (
          <span key={`line-${index}`}>
            {index > 0 && <br />}
            {renderInlineMarkdown(line, `inline-${blocks.length}-${index}`)}
          </span>
        ))}
      </p>
    );
    paragraph = [];
  }

  function flushList() {
    if (!list) return;
    const List = list.ordered ? "ol" : "ul";
    blocks.push(
      <List className="markdown-list" key={`list-${blocks.length}`}>
        {list.items.map((item, index) => (
          <li key={`item-${index}`}>
            {renderInlineMarkdown(item, `list-inline-${blocks.length}-${index}`)}
          </li>
        ))}
      </List>
    );
    list = null;
  }

  function addListItem(item, ordered) {
    flushParagraph();
    if (list && list.ordered !== ordered) flushList();
    if (!list) list = { ordered, items: [] };
    list.items.push(item);
  }

  lines.forEach((line) => {
    const trimmed = line.trim();
    if (!trimmed) {
      flushParagraph();
      flushList();
      return;
    }

    if (/^(?:-{3,}|_{3,}|\*{3,})$/.test(trimmed)) {
      flushParagraph();
      flushList();
      blocks.push(<hr className="markdown-rule" key={`rule-${blocks.length}`} />);
      return;
    }

    const heading = trimmed.match(/^(#{1,6})\s+(.+?)\s*#*$/);
    if (heading) {
      flushParagraph();
      flushList();
      const Heading = `h${heading[1].length}`;
      blocks.push(
        <Heading className="markdown-heading" key={`heading-${blocks.length}`}>
          {renderInlineMarkdown(heading[2], `heading-inline-${blocks.length}`)}
        </Heading>
      );
      return;
    }

    const unordered = trimmed.match(/^[-*+•]\s+(.+)$/);
    const ordered = trimmed.match(/^\d+[.)]\s+(.+)$/);
    const match = unordered || ordered;
    if (match) {
      addListItem(match[1], Boolean(ordered));
      return;
    }

    flushList();
    paragraph.push(trimmed);
  });

  flushParagraph();
  flushList();
  return <div className="markdown-content">{blocks}</div>;
}

function HistoryQuestionDetails({
  item,
  onSubmitAnswer,
  submitting,
  error,
  onReevaluate,
  reevaluatingAnswerId,
  reevaluationError,
}) {
  const [newAnswer, setNewAnswer] = useState("");

  async function handleSubmit(event) {
    event.preventDefault();
    const trimmedAnswer = newAnswer.trim();
    if (!trimmedAnswer || submitting) return;
    const saved = await onSubmitAnswer(trimmedAnswer);
    if (saved) setNewAnswer("");
  }

  return (
    <section className="evaluation-card">
      <span className="question-label">QUESTION HISTORY</span>
      <h3>{item.question}</h3>
      {!item.answers?.length ? (
        <p className="helper-note">No answers have been submitted for this question.</p>
      ) : (
        <>
          <div className="sample-answer">
            <h4>Score comparison by attempt</h4>
            {item.answers.map((attempt) => (
              <div className="sample-answer" key={`comparison-${attempt.answer_id}`}>
                <div className="score-row">
                  <strong>Attempt {attempt.attempt_no}</strong>
                  <span>
                    {attempt.evaluations?.length
                      ? attempt.evaluations.map((evaluation, index) => {
                          const hasScore =
                            typeof evaluation.score === "number" &&
                            Number.isFinite(evaluation.score);
                          return (
                            <span key={evaluation.evaluation_id}>
                              {index > 0 ? " · " : ""}
                              Evaluation {index + 1}:{" "}
                              {hasScore ? `${evaluation.score}/100` : "Score unavailable"}
                            </span>
                          );
                        })
                      : "No evaluation or score recorded"}
                  </span>
                </div>
              </div>
            ))}
          </div>
          {item.answers.map((attempt) => (
            <article className="sample-answer" key={attempt.answer_id}>
              <div className="score-row">
                <h4>Attempt {attempt.attempt_no}</h4>
                <button
                  className="button button-outline"
                  type="button"
                  onClick={() => onReevaluate(attempt.answer_id)}
                  disabled={Boolean(reevaluatingAnswerId) || submitting}
                >
                  {reevaluatingAnswerId === attempt.answer_id
                    ? "Re-evaluating…"
                    : "Re-evaluate"}
                </button>
              </div>
              <p>{attempt.answer}</p>
              {!attempt.evaluations?.length ? (
                <span className="sample-answer-note">No evaluation recorded.</span>
              ) : (
                attempt.evaluations.map((evaluation) => {
                  const feedback = evaluation.feedback || {};
                  const hasScore =
                    typeof evaluation.score === "number" &&
                    Number.isFinite(evaluation.score);
                  return (
                    <div className="sample-answer" key={evaluation.evaluation_id}>
                      <h4>
                        Evaluation ·{" "}
                        {hasScore ? `${evaluation.score}/100` : "Score unavailable"} ·{" "}
                        {new Date(evaluation.created_at).toLocaleString()}
                      </h4>
                      {feedback.strengths?.length > 0 && (
                        <>
                          <strong>Strengths</strong>
                          <ul>{feedback.strengths.map((text, index) => <li key={`${index}-${text}`}>{text}</li>)}</ul>
                        </>
                      )}
                      {feedback.improvements?.length > 0 && (
                        <>
                          <strong>Improvements</strong>
                          <ul>{feedback.improvements.map((text, index) => <li key={`${index}-${text}`}>{text}</li>)}</ul>
                        </>
                      )}
                      {(feedback.suggested_answer || feedback.improved_sample_answer) && (
                        <p>{feedback.suggested_answer || feedback.improved_sample_answer}</p>
                      )}
                      {!feedback.strengths?.length &&
                        !feedback.improvements?.length &&
                        !feedback.suggested_answer &&
                        !feedback.improved_sample_answer && (
                          <span className="sample-answer-note">
                            No additional feedback recorded.
                          </span>
                        )}
                    </div>
                  );
                })
              )}
            </article>
          ))}
        </>
      )}
      {reevaluatingAnswerId && (
        <p className="loading-note" role="status">
          Saving a new evaluation without replacing previous feedback…
        </p>
      )}
      <ErrorMessage>{reevaluationError}</ErrorMessage>
      <form className="answer-form" onSubmit={handleSubmit}>
        <label htmlFor={`history-answer-${item.question_id}`}>New answer attempt</label>
        <textarea
          id={`history-answer-${item.question_id}`}
          value={newAnswer}
          onChange={(event) => setNewAnswer(event.target.value)}
          rows="4"
          maxLength="5000"
          placeholder="Write another answer to this question…"
          disabled={submitting}
        />
        <button
          className="button button-primary"
          type="submit"
          disabled={!newAnswer.trim() || submitting}
        >
          {submitting ? "Submitting and evaluating…" : "Submit new attempt"}
        </button>
        <ErrorMessage>{error}</ErrorMessage>
      </form>
    </section>
  );
}

export default function Home() {
  const [backendStatus, setBackendStatus] = useState("checking");
  const [resume, setResume] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");

  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState([]);
  const [sendingChat, setSendingChat] = useState(false);
  const [chatError, setChatError] = useState("");

  const [interview, setInterview] = useState(null);
  const [answer, setAnswer] = useState("");
  const [answerSubmitted, setAnswerSubmitted] = useState(false);
  const [evaluation, setEvaluation] = useState(null);
  const [interviewBusy, setInterviewBusy] = useState("");
  const [interviewError, setInterviewError] = useState("");

  const [preparationMode, setPreparationMode] = useState("dsa");
  const [preparationTopic, setPreparationTopic] = useState("");
  const [preparationSessionId, setPreparationSessionId] = useState("");
  const [preparationQuestion, setPreparationQuestion] = useState(null);
  const [preparationAnswer, setPreparationAnswer] = useState("");
  const [preparationEvaluation, setPreparationEvaluation] = useState(null);
  const [preparationBusy, setPreparationBusy] = useState("");
  const [preparationError, setPreparationError] = useState("");

  const [practiceHistoryIds, setPracticeHistoryIds] = useState(() => {
    try {
      const stored = JSON.parse(
        window.localStorage.getItem("placementprep-practice-sessions") || "[]"
      );
      return Array.isArray(stored)
        ? stored.filter((item) => item?.session_id && item?.mode)
        : [];
    } catch {
      return [];
    }
  });
  const [practiceHistory, setPracticeHistory] = useState(null);
  const [practiceQuestionHistory, setPracticeQuestionHistory] = useState(null);
  const [practiceHistoryBusy, setPracticeHistoryBusy] = useState(false);
  const [practiceHistoryError, setPracticeHistoryError] = useState("");
  const [practiceAttemptBusy, setPracticeAttemptBusy] = useState(false);
  const [practiceAttemptError, setPracticeAttemptError] = useState("");
  const [practiceReevaluatingAnswerId, setPracticeReevaluatingAnswerId] =
    useState("");
  const [practiceReevaluationError, setPracticeReevaluationError] = useState("");

  const [interviewHistory, setInterviewHistory] = useState([]);
  const [interviewHistorySession, setInterviewHistorySession] = useState(null);
  const [interviewQuestionHistory, setInterviewQuestionHistory] = useState(null);
  const [interviewHistoryBusy, setInterviewHistoryBusy] = useState(false);
  const [interviewHistoryError, setInterviewHistoryError] = useState("");
  const [interviewAttemptBusy, setInterviewAttemptBusy] = useState(false);
  const [interviewAttemptError, setInterviewAttemptError] = useState("");
  const [interviewReevaluatingAnswerId, setInterviewReevaluatingAnswerId] =
    useState("");
  const [interviewReevaluationError, setInterviewReevaluationError] = useState("");

  useEffect(() => {
    checkHealth()
      .then((data) => setBackendStatus(data.status === "ok" ? "online" : "unavailable"))
      .catch(() => setBackendStatus("offline"));
  }, []);

  async function handleUpload(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const file = form.elements.resume.files?.[0];
    if (!file) {
      setUploadError("Choose a resume PDF to upload.");
      return;
    }
    if (file.type && file.type !== "application/pdf") {
      setUploadError("Please choose a PDF file.");
      return;
    }

    setUploading(true);
    setUploadError("");
    try {
      const uploaded = await uploadResume(file);
      setResume(uploaded);
      setMessages([]);
      setQuestion("");
      setInterview(null);
      setAnswer("");
      setAnswerSubmitted(false);
      setEvaluation(null);
      form.reset();
    } catch (error) {
      setUploadError(error.message);
    } finally {
      setUploading(false);
    }
  }

  async function handleChat(event) {
    event.preventDefault();
    const trimmedQuestion = question.trim();
    if (!trimmedQuestion || !resume) return;

    setSendingChat(true);
    setChatError("");
    setQuestion("");
    try {
      const result = await sendChatMessage(resume.resume_id, trimmedQuestion);
      setMessages((current) => [
        ...current,
        { question: trimmedQuestion, answer: result.answer },
      ]);
    } catch (error) {
      setQuestion(trimmedQuestion);
      setChatError(error.message);
    } finally {
      setSendingChat(false);
    }
  }

  async function handleStartInterview() {
    if (!resume) return;
    setInterviewBusy("start");
    setInterviewError("");
    try {
      const session = await startInterview(resume.resume_id);
      setInterview(session);
      setAnswer("");
      setAnswerSubmitted(false);
      setEvaluation(null);
    } catch (error) {
      setInterviewError(error.message);
    } finally {
      setInterviewBusy("");
    }
  }

  async function handleSubmitAnswer(event) {
    event.preventDefault();
    const trimmedAnswer = answer.trim();
    if (!trimmedAnswer || !interview) return;

    setInterviewBusy("answer");
    setInterviewError("");
    try {
      await submitInterviewAnswer(interview.session_id, trimmedAnswer);
      setAnswer(trimmedAnswer);
      setAnswerSubmitted(true);
      setEvaluation(null);
    } catch (error) {
      setInterviewError(error.message);
    } finally {
      setInterviewBusy("");
    }
  }

  async function handleEvaluate() {
    if (!interview || !answerSubmitted) return;
    setInterviewBusy("evaluate");
    setEvaluation(null);
    setInterviewError("");
    try {
      const result = await evaluateInterviewAnswer(interview.session_id);
      setEvaluation(result);
    } catch (error) {
      setInterviewError(error.message);
    } finally {
      setInterviewBusy("");
    }
  }

  async function handleNextQuestion() {
    if (!interview || !answerSubmitted) return;
    setInterviewBusy("next");
    setInterviewError("");
    try {
      const result = await requestNextQuestion(interview.session_id);
      setInterview((current) => ({ ...current, question: result.question }));
      setAnswer("");
      setAnswerSubmitted(false);
      setEvaluation(null);
    } catch (error) {
      setInterviewError(error.message);
    } finally {
      setInterviewBusy("");
    }
  }

  function handlePreparationModeChange(event) {
    setPreparationMode(event.target.value);
    setPreparationSessionId("");
    setPreparationQuestion(null);
    setPreparationAnswer("");
    setPreparationEvaluation(null);
    setPreparationError("");
  }

  async function handleGeneratePreparationQuestion() {
    setPreparationBusy("question");
    setPreparationError("");
    setPreparationQuestion(null);
    setPreparationAnswer("");
    setPreparationEvaluation(null);
    try {
      const result = await generatePreparationQuestion(
        preparationMode,
        preparationTopic.trim() || undefined,
        preparationSessionId || undefined
      );
      setPreparationSessionId(result.session_id);
      setPreparationQuestion(result);
      const savedSessions = [
        ...practiceHistoryIds.filter(
          (item) => item.session_id !== result.session_id
        ),
        { session_id: result.session_id, mode: result.mode },
      ];
      setPracticeHistoryIds(savedSessions);
      try {
        window.localStorage.setItem(
          "placementprep-practice-sessions",
          JSON.stringify(savedSessions)
        );
      } catch {
        setPreparationError(
          "Question generated, but the practice session could not be saved in this browser."
        );
      }
    } catch (error) {
      setPreparationError(error.message);
    } finally {
      setPreparationBusy("");
    }
  }

  async function handleEvaluatePreparationAnswer(event) {
    event.preventDefault();
    const trimmedAnswer = preparationAnswer.trim();
    if (!preparationQuestion || !trimmedAnswer) return;

    setPreparationBusy("evaluate");
    setPreparationError("");
    setPreparationEvaluation(null);
    try {
      const result = await evaluatePreparationAnswer(
        preparationMode,
        preparationQuestion.question,
        trimmedAnswer,
        preparationSessionId,
        preparationQuestion.question_id
      );
      setPreparationAnswer(trimmedAnswer);
      setPreparationEvaluation(result);
    } catch (error) {
      setPreparationError(error.message);
    } finally {
      setPreparationBusy("");
    }
  }

  async function handleOpenPracticeHistory(sessionId) {
    setPracticeHistoryBusy(true);
    setPracticeHistoryError("");
    setPracticeHistory(null);
    setPracticeQuestionHistory(null);
    try {
      const result = await getPreparationHistory(sessionId);
      setPracticeHistory(result);
    } catch (error) {
      if (error.status === 404) {
        const savedSessions = practiceHistoryIds.filter(
          (item) => item.session_id !== sessionId
        );
        setPracticeHistoryIds(savedSessions);
        try {
          window.localStorage.setItem(
            "placementprep-practice-sessions",
            JSON.stringify(savedSessions)
          );
          setPracticeHistoryError(
            "This practice session is no longer available and was removed from saved history in this browser."
          );
        } catch {
          setPracticeHistoryError(
            "This practice session is no longer available. The stale saved link could not be removed from this browser."
          );
        }
      } else {
        setPracticeHistoryError(error.message);
      }
    } finally {
      setPracticeHistoryBusy(false);
    }
  }

  async function handleOpenPracticeQuestion(questionId) {
    setPracticeHistoryBusy(true);
    setPracticeHistoryError("");
    try {
      const result = await getPreparationQuestionHistory(questionId);
      setPracticeQuestionHistory(result);
    } catch (error) {
      setPracticeHistoryError(error.message);
    } finally {
      setPracticeHistoryBusy(false);
    }
  }

  async function handleSubmitPracticeHistoryAnswer(questionId, answer) {
    setPracticeAttemptBusy(true);
    setPracticeAttemptError("");
    let result;
    try {
      result = await submitHistoricalPreparationAnswer(questionId, answer);
    } catch (error) {
      setPracticeAttemptError(error.message);
      setPracticeAttemptBusy(false);
      return false;
    }

    let evaluationError = "";
    try {
      await reevaluateHistoricalPreparationAnswer(result.answer_id);
    } catch (error) {
      evaluationError = error.message;
    }

    try {
      const detail = await getPreparationQuestionHistory(questionId);
      setPracticeQuestionHistory(detail);
    } catch (error) {
      setPracticeAttemptError(
        `Answer saved, but history could not be refreshed: ${error.message}`
      );
      return true;
    } finally {
      setPracticeAttemptBusy(false);
    }
    if (evaluationError) {
      setPracticeAttemptError(
        `Answer saved, but evaluation failed: ${evaluationError}`
      );
    }
    setPracticeAttemptBusy(false);
    return true;
  }

  async function handleReevaluatePracticeAnswer(questionId, answerId) {
    setPracticeReevaluatingAnswerId(answerId);
    setPracticeReevaluationError("");
    try {
      await reevaluateHistoricalPreparationAnswer(answerId);
      const detail = await getPreparationQuestionHistory(questionId);
      setPracticeQuestionHistory(detail);
    } catch (error) {
      setPracticeReevaluationError(
        error.message || "Could not re-evaluate this answer."
      );
    } finally {
      setPracticeReevaluatingAnswerId("");
    }
  }

  async function handleLoadInterviewHistory() {
    if (!resume?.resume_id) return;
    setInterviewHistoryBusy(true);
    setInterviewHistoryError("");
    setInterviewHistorySession(null);
    setInterviewQuestionHistory(null);
    try {
      const result = await getInterviewHistory(resume.resume_id);
      setInterviewHistory(result.sessions || []);
    } catch (error) {
      setInterviewHistoryError(error.message);
    } finally {
      setInterviewHistoryBusy(false);
    }
  }

  async function handleOpenInterviewHistory(sessionId) {
    setInterviewHistoryBusy(true);
    setInterviewHistoryError("");
    setInterviewQuestionHistory(null);
    try {
      const result = await getInterviewHistorySession(sessionId);
      setInterviewHistorySession(result);
    } catch (error) {
      setInterviewHistoryError(error.message);
    } finally {
      setInterviewHistoryBusy(false);
    }
  }

  async function handleOpenInterviewQuestion(questionId) {
    setInterviewHistoryBusy(true);
    setInterviewHistoryError("");
    try {
      const result = await getInterviewQuestionHistory(questionId);
      setInterviewQuestionHistory(result);
    } catch (error) {
      setInterviewHistoryError(error.message);
    } finally {
      setInterviewHistoryBusy(false);
    }
  }

  async function handleSubmitInterviewHistoryAnswer(questionId, answer) {
    setInterviewAttemptBusy(true);
    setInterviewAttemptError("");
    let result;
    try {
      result = await submitHistoricalInterviewAnswer(questionId, answer);
    } catch (error) {
      setInterviewAttemptError(error.message);
      setInterviewAttemptBusy(false);
      return false;
    }

    let evaluationError = "";
    try {
      await reevaluateHistoricalInterviewAnswer(result.answer_id);
    } catch (error) {
      evaluationError = error.message;
    }

    try {
      const detail = await getInterviewQuestionHistory(questionId);
      setInterviewQuestionHistory(detail);
    } catch (error) {
      setInterviewAttemptError(
        `Answer saved, but history could not be refreshed: ${error.message}`
      );
      setInterviewAttemptBusy(false);
      return true;
    }
    if (evaluationError) {
      setInterviewAttemptError(
        `Answer saved, but evaluation failed: ${evaluationError}`
      );
    }
    setInterviewAttemptBusy(false);
    return true;
  }

  async function handleReevaluateInterviewAnswer(questionId, answerId) {
    setInterviewReevaluatingAnswerId(answerId);
    setInterviewReevaluationError("");
    try {
      await reevaluateHistoricalInterviewAnswer(answerId);
      const detail = await getInterviewQuestionHistory(questionId);
      setInterviewQuestionHistory(detail);
    } catch (error) {
      setInterviewReevaluationError(
        error.message || "Could not re-evaluate this answer."
      );
    } finally {
      setInterviewReevaluatingAnswerId("");
    }
  }

  const interviewWorking = Boolean(interviewBusy);

  return (
    <main className="app-shell">
      <header className="topbar">
        <a className="brand" href="#" aria-label="PlacementPrepAI home">
          <span className="brand-mark">P</span>
          <span>PlacementPrep<span className="brand-accent">AI</span></span>
        </a>
        <div className={`connection connection-${backendStatus}`} aria-live="polite">
          <span className="connection-dot" />
          Backend {backendStatus}
        </div>
      </header>

      <section className="hero">
        <p className="eyebrow">YOUR NEXT OPPORTUNITY STARTS HERE</p>
        <h1>Prepare with confidence.</h1>
        <p className="hero-copy">
          Practice smarter with guidance grounded in your own experience.
        </p>
      </section>

      <div className="workspace">
        <aside className="resume-panel panel">
          <div className="section-heading">
            <span className="step-number">01</span>
            <div>
              <h2>Your resume</h2>
              <p>Start with the experience you already have.</p>
            </div>
          </div>

          <form onSubmit={handleUpload} className="upload-form">
            <label className="file-picker" htmlFor="resume-file">
              <span className="upload-icon" aria-hidden="true">↑</span>
              <span className="file-picker-title">Choose a resume PDF</span>
              <span className="file-picker-hint">PDF format</span>
            </label>
            <input
              id="resume-file"
              name="resume"
              type="file"
              accept="application/pdf,.pdf"
              disabled={uploading}
            />
            <button className="button button-primary button-full" disabled={uploading}>
              {uploading ? "Uploading resume…" : "Upload resume"}
            </button>
          </form>
          <ErrorMessage>{uploadError}</ErrorMessage>

          {resume ? (
            <div className="resume-result" role="status">
              <span className="file-badge" aria-hidden="true">PDF</span>
              <div className="resume-details">
                <strong>{resume.filename || "Resume uploaded"}</strong>
                <span>{resume.chunks_count} searchable chunks</span>
              </div>
              <span className="success-mark" aria-label="Uploaded">✓</span>
              <div className="resume-id">
                <span>RESUME ID</span>
                <code>{resume.resume_id}</code>
              </div>
            </div>
          ) : (
            <p className="helper-note">
              Upload a PDF to unlock resume-aware chat and interview practice.
            </p>
          )}
        </aside>

        <div className="tools-grid">
          <section className="panel tool-panel">
            <div className="section-heading">
              <span className="step-number">02</span>
              <div>
                <h2>Ask your AI coach</h2>
                <p>Get answers informed by your resume.</p>
              </div>
            </div>

            <div className="chat-thread" aria-live="polite" aria-relevant="additions">
              {messages.length === 0 && !sendingChat ? (
                <div className="empty-state">
                  <span className="empty-symbol" aria-hidden="true">✳</span>
                  <p>Your coaching conversation starts here.</p>
                  <span>Try asking how to describe a project or highlight a skill.</span>
                </div>
              ) : (
                messages.map((message, index) => (
                  <article className="message-pair" key={`${index}-${message.question}`}>
                    <div className="message message-user">
                      <span className="message-label">YOU</span>
                      <p>{message.question}</p>
                    </div>
                    <div className="message message-coach">
                      <span className="message-label">AI COACH</span>
                      <MarkdownContent>{message.answer}</MarkdownContent>
                    </div>
                  </article>
                ))
              )}
              {sendingChat && (
                <p className="loading-note" role="status">Your coach is thinking…</p>
              )}
            </div>
            <ErrorMessage>{chatError}</ErrorMessage>

            <form onSubmit={handleChat} className="composer">
              <label className="sr-only" htmlFor="chat-question">Ask your resume coach</label>
              <textarea
                id="chat-question"
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                placeholder={resume ? "Ask something about your experience…" : "Upload a resume to start chatting"}
                rows="2"
                maxLength="2000"
                disabled={!resume || sendingChat}
              />
              <button
                className="button button-dark"
                type="submit"
                disabled={!resume || !question.trim() || sendingChat}
              >
                {sendingChat ? "Sending…" : "Send"}
              </button>
            </form>
          </section>

          <section className="panel tool-panel interview-panel">
            <div className="section-heading">
              <span className="step-number">03</span>
              <div>
                <h2>Mock interview</h2>
                <p>One question at a time, at your pace.</p>
              </div>
            </div>

            {!interview ? (
              <div className="interview-intro">
                <div className="interview-art" aria-hidden="true">
                  <span className="art-orbit orbit-one" />
                  <span className="art-orbit orbit-two" />
                  <span className="art-center">Q</span>
                </div>
                <p>Get a resume-informed question and practice your answer.</p>
                <button
                  className="button button-primary button-full"
                  type="button"
                  onClick={handleStartInterview}
                  disabled={!resume || interviewWorking}
                >
                  {interviewBusy === "start" ? "Starting session…" : "Start interview"}
                </button>
                {!resume && <span className="inline-hint">Upload your resume first.</span>}
              </div>
            ) : (
              <div className="interview-session">
                <div className="session-meta">
                  <span className="live-indicator">LIVE SESSION</span>
                  <span className="session-id">ID · {interview.session_id.slice(0, 8)}</span>
                </div>
                <div className="question-card">
                  <span className="question-label">YOUR QUESTION</span>
                  <h3>{interview.question}</h3>
                </div>
                <form onSubmit={handleSubmitAnswer} className="answer-form">
                  <label htmlFor="interview-answer">Your answer</label>
                  <textarea
                    id="interview-answer"
                    value={answer}
                    onChange={(event) => setAnswer(event.target.value)}
                    placeholder="Take a moment to structure your answer…"
                    rows="4"
                    maxLength="5000"
                    disabled={answerSubmitted || interviewWorking}
                  />
                  {!answerSubmitted ? (
                    <button
                      className="button button-primary button-full"
                      type="submit"
                      disabled={!answer.trim() || interviewWorking}
                    >
                      {interviewBusy === "answer" ? "Saving answer…" : "Submit answer"}
                    </button>
                  ) : (
                    <div className="session-actions">
                      <button
                        className="button button-outline"
                        type="button"
                        onClick={handleEvaluate}
                        disabled={interviewWorking}
                      >
                        {interviewBusy === "evaluate" ? "Evaluating…" : "Evaluate answer"}
                      </button>
                      <button
                        className="button button-dark"
                        type="button"
                        onClick={handleNextQuestion}
                        disabled={interviewWorking}
                      >
                        {interviewBusy === "next" ? "Loading…" : "Next question"}
                      </button>
                    </div>
                  )}
                </form>
                {interviewBusy === "evaluate" && (
                  <div className="evaluation-loading" role="status" aria-live="polite">
                    <span className="loading-spinner" aria-hidden="true" />
                    Evaluating your answer…
                  </div>
                )}
                {evaluation && interviewBusy !== "evaluate" && (
                  <section className="evaluation-card" aria-live="polite">
                    <div className="score-row">
                      <div>
                        <span className="question-label">ANSWER FEEDBACK</span>
                        <h3>Interview coaching</h3>
                      </div>
                      <div className="score-badge">
                        <strong>{evaluation.score}</strong><span>/100</span>
                      </div>
                    </div>
                    <p className="feedback-disclaimer">
                      Coaching suggestions are not verification of your actual
                      implementation. Only use details that accurately reflect
                      your work.
                    </p>
                    <div className="feedback-columns">
                      <div>
                        <h4>Strengths</h4>
                        <ul>{evaluation.strengths.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul>
                      </div>
                      <div>
                        <h4>Opportunities</h4>
                        <ul>{evaluation.improvements.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul>
                      </div>
                    </div>
                    <div className="sample-answer">
                      <h4>Suggested sample answer</h4>
                      <span className="sample-answer-note">
                        Example wording only — adapt it to your verified experience.
                      </span>
                      <p>{evaluation.improved_sample_answer}</p>
                    </div>
                  </section>
                )}
              </div>
            )}
            <ErrorMessage>{interviewError}</ErrorMessage>
          </section>

          <section className="panel tool-panel">
            <div className="section-heading">
              <span className="step-number">04</span>
              <div>
                <h2>DSA, SQL &amp; HR practice</h2>
                <p>Practice focused questions and get structured feedback.</p>
              </div>
            </div>

            <div className="answer-form">
              <label htmlFor="preparation-mode">Practice mode</label>
              <select
                id="preparation-mode"
                value={preparationMode}
                onChange={handlePreparationModeChange}
                disabled={Boolean(preparationBusy)}
              >
                <option value="dsa">DSA</option>
                <option value="sql">SQL</option>
                <option value="hr">HR</option>
              </select>
              <label htmlFor="preparation-topic">Optional topic</label>
              <input
                id="preparation-topic"
                type="text"
                value={preparationTopic}
                onChange={(event) => setPreparationTopic(event.target.value)}
                placeholder="e.g. graphs, joins, teamwork"
                maxLength="200"
                disabled={Boolean(preparationBusy)}
              />
              <button
                className="button button-primary button-full"
                type="button"
                onClick={handleGeneratePreparationQuestion}
                disabled={Boolean(preparationBusy)}
              >
                {preparationBusy === "question"
                  ? "Generating question…"
                  : preparationQuestion
                    ? "Generate next question"
                    : "Generate question"}
              </button>
            </div>

            {preparationBusy === "evaluate" && (
              <div className="evaluation-loading" role="status" aria-live="polite">
                <span className="loading-spinner" aria-hidden="true" />
                Submitting and evaluating your answer…
              </div>
            )}

            {preparationQuestion && (
              <div className="interview-session">
                <div className="question-card">
                  <span className="question-label">
                    {preparationMode.toUpperCase()} QUESTION
                  </span>
                  <h3>{preparationQuestion.question}</h3>
                </div>
                <form
                  onSubmit={handleEvaluatePreparationAnswer}
                  className="answer-form"
                >
                  <label htmlFor="preparation-answer">Your answer</label>
                  <textarea
                    id="preparation-answer"
                    value={preparationAnswer}
                    onChange={(event) => setPreparationAnswer(event.target.value)}
                    placeholder="Write your answer…"
                    rows="4"
                    maxLength="5000"
                    disabled={Boolean(preparationBusy)}
                  />
                  <button
                    className="button button-dark button-full"
                    type="submit"
                    disabled={!preparationAnswer.trim() || Boolean(preparationBusy)}
                  >
                    {preparationBusy === "evaluate"
                      ? "Submitting answer…"
                      : "Submit & evaluate"}
                  </button>
                </form>
              </div>
            )}

            {preparationEvaluation && preparationBusy !== "evaluate" && (
              <section className="evaluation-card" aria-live="polite">
                <div className="score-row">
                  <div>
                    <span className="question-label">ANSWER FEEDBACK</span>
                    <h3>Practice coaching</h3>
                  </div>
                  <div className="score-badge">
                    <strong>{preparationEvaluation.score}</strong>
                    <span>/100</span>
                  </div>
                </div>
                <div className="feedback-columns">
                  <div>
                    <h4>Strengths</h4>
                    <ul>
                      {preparationEvaluation.strengths.map((item, index) => (
                        <li key={`${index}-${item}`}>{item}</li>
                      ))}
                    </ul>
                  </div>
                  <div>
                    <h4>Opportunities</h4>
                    <ul>
                      {preparationEvaluation.improvements.map((item, index) => (
                        <li key={`${index}-${item}`}>{item}</li>
                      ))}
                    </ul>
                  </div>
                </div>
                <div className="sample-answer">
                  <h4>Suggested answer / explanation</h4>
                  <p>{preparationEvaluation.suggested_answer}</p>
                </div>
              </section>
            )}
            <ErrorMessage>{preparationError}</ErrorMessage>
          </section>

          <section className="panel tool-panel">
            <div className="section-heading">
              <span className="step-number">05</span>
              <div>
                <h2>Practice history</h2>
                <p>Revisit saved DSA, SQL, and HR sessions.</p>
                <p className="helper-note">
                  On Render Free, practice history may be lost when the service
                  restarts or redeploys.
                </p>
              </div>
            </div>
            {practiceHistoryIds.length === 0 ? (
              <p className="helper-note">Generated practice sessions will appear here.</p>
            ) : (
              <div className="answer-form">
                {practiceHistoryIds.map((item) => (
                  <button
                    className="button button-outline"
                    key={item.session_id}
                    type="button"
                    onClick={() => handleOpenPracticeHistory(item.session_id)}
                    disabled={practiceHistoryBusy}
                  >
                    {item.mode.toUpperCase()} session · {item.session_id.slice(0, 8)}
                  </button>
                ))}
              </div>
            )}
            {practiceHistoryBusy && (
              <p className="loading-note" role="status">Loading practice history…</p>
            )}
            <ErrorMessage>{practiceHistoryError}</ErrorMessage>
            {practiceHistory && (
              <div className="interview-session">
                <div className="question-card">
                  <span className="question-label">
                    {practiceHistory.mode?.toUpperCase()} SESSION
                  </span>
                  <p>{practiceHistory.session_id}</p>
                </div>
                <div className="answer-form">
                  {practiceHistory.questions.map((item) => (
                    <button
                      className="button button-outline"
                      key={item.question_id}
                      type="button"
                      onClick={() => handleOpenPracticeQuestion(item.question_id)}
                      disabled={practiceHistoryBusy}
                    >
                      Question {item.ordinal}: {item.question}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {practiceQuestionHistory && (
              <HistoryQuestionDetails
                item={practiceQuestionHistory}
                onSubmitAnswer={(value) =>
                  handleSubmitPracticeHistoryAnswer(
                    practiceQuestionHistory.question_id,
                    value
                  )
                }
                submitting={practiceAttemptBusy}
                error={practiceAttemptError}
                onReevaluate={(answerId) =>
                  handleReevaluatePracticeAnswer(
                    practiceQuestionHistory.question_id,
                    answerId
                  )
                }
                reevaluatingAnswerId={practiceReevaluatingAnswerId}
                reevaluationError={practiceReevaluationError}
              />
            )}
          </section>

          <section className="panel tool-panel">
            <div className="section-heading">
              <span className="step-number">06</span>
              <div>
                <h2>Mock interview history</h2>
                <p>Review previous questions, answers, and feedback.</p>
              </div>
            </div>
            <button
              className="button button-primary"
              type="button"
              onClick={handleLoadInterviewHistory}
              disabled={!resume || interviewHistoryBusy}
            >
              {interviewHistoryBusy ? "Loading history…" : "Load interview history"}
            </button>
            {!resume && (
              <p className="helper-note">Upload the resume used for an interview to view its history.</p>
            )}
            {interviewHistory.map((item) => (
              <button
                className="button button-outline"
                key={item.session_id}
                type="button"
                onClick={() => handleOpenInterviewHistory(item.session_id)}
                disabled={interviewHistoryBusy}
              >
                Interview · {new Date(item.created_at).toLocaleString()} · {item.question_count} questions
              </button>
            ))}
            {interviewHistoryBusy && (
              <p className="loading-note" role="status">Loading interview history…</p>
            )}
            <ErrorMessage>{interviewHistoryError}</ErrorMessage>
            {interviewHistorySession && (
              <div className="interview-session">
                <div className="answer-form">
                  {interviewHistorySession.questions.map((item) => (
                    <button
                      className="button button-outline"
                      key={item.question_id}
                      type="button"
                      onClick={() => handleOpenInterviewQuestion(item.question_id)}
                      disabled={interviewHistoryBusy}
                    >
                      Question {item.ordinal}: {item.question}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {interviewQuestionHistory && (
              <HistoryQuestionDetails
                item={interviewQuestionHistory}
                onSubmitAnswer={(value) =>
                  handleSubmitInterviewHistoryAnswer(
                    interviewQuestionHistory.question_id,
                    value
                  )
                }
                submitting={interviewAttemptBusy}
                error={interviewAttemptError}
                onReevaluate={(answerId) =>
                  handleReevaluateInterviewAnswer(
                    interviewQuestionHistory.question_id,
                    answerId
                  )
                }
                reevaluatingAnswerId={interviewReevaluatingAnswerId}
                reevaluationError={interviewReevaluationError}
              />
            )}
          </section>
        </div>
      </div>
      <footer className="footer-note">
        <span>PLACEMENTPREP AI</span>
        <span>Practice built around your experience.</span>
      </footer>
    </main>
  );
}
