import { useState, useRef, useEffect } from "react";import "./ChatBox.css";
import ReactMarkdown from "react-markdown";

const API_URL = "http://127.0.0.1:8000/chat/";
function ChatBox() {
    const [question, setQuestion] = useState("");
    const [messages, setMessages] = useState([]);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState("");
    const messagesEndRef = useRef(null);

    useEffect(() => {
    messagesEndRef.current?.scrollIntoView({
        behavior: "smooth"
    });
}, [messages, isLoading]);

    const handleSend = async () => {
        if (question.trim() === "") {
            return;
        }

        const userQuestion = question;
        setIsLoading(true);

        setMessages([
            ...messages,
            {
                role: "user",
                text: userQuestion
            }
        ]);

        setQuestion("");

        // console.log("Sending question to backend:", userQuestion);
        try {
            const response = await fetch(API_URL, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    question: userQuestion
                })
            });

            const data = await response.json();

            setIsLoading(false);

            console.log("Backend response:", data);
            console.log("AI answer:", data.answer);

            setMessages([
                ...messages,
                {
                    role: "user",
                    text: userQuestion
                },
                {
                    role: "ai",
                    text: data.answer
                }
            ]);
        } catch (error) {
            setIsLoading(false);
            setError("Sorry, something went wrong. Please try again.");
            console.log("API error:", error);
        }
    };

    const handleKeyDown = (e) => {
        if (e.key === "Enter") {
            handleSend();
        }
    };
    return (
        <section className="chat-box">
            <h2>Ask PlacementPrepAI</h2>

            <p>
                Ask anything about your resume, skills, projects,
                education, or placement preparation.
            </p>

            <div className="chat-input">
                <input
                    type="text"
                    placeholder="Type your question..."
                    value={question}
                    onChange={(e) => setQuestion(e.target.value)}
                    onKeyDown={handleKeyDown}
                    disabled={isLoading}
                />

                <button onClick={handleSend} disabled={isLoading}>
                    Send
                </button>
            </div>

            <div className="messages">
                {messages.map((message, index) => (
                    <div
                        key={index}
                        className={message.role === "user" ? "user-message" : "ai-message"}
                    >
                        {message.role === "ai" ? (
                            <ReactMarkdown>{message.text}</ReactMarkdown>
                        ) : (
                            <p>{message.text}</p>
                        )}
                    </div>
                ))}

                {isLoading && (
                    <p className="ai-message">
                        AI is thinking...
                    </p>
                )}
                {error && (
                    <p className="error-message">
                        {error}
                    </p>
                )}

                <div ref={messagesEndRef}></div>
            </div>

        </section>
    );
}

export default ChatBox;