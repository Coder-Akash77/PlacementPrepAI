import { useEffect, useState } from "react";
import {
  checkHealth,
  evaluateInterviewAnswer,
  requestNextQuestion,
  sendChatMessage,
  startInterview,
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
        </div>
      </div>
      <footer className="footer-note">
        <span>PLACEMENTPREP AI</span>
        <span>Practice built around your experience.</span>
      </footer>
    </main>
  );
}
