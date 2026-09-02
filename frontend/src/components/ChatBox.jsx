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

setError("");
setIsLoading(true);

        setMessages((previousMessages) => [
    ...previousMessages,
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

            setMessages((previousMessages) => [
    ...previousMessages,
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

            {messages.length === 0 && (
    <div className="suggested-questions">
        <p className="suggested-title">Try asking</p>

        <div className="suggestion-list">
            <button onClick={() => setQuestion("What projects are on my resume?")}>
                What projects are on my resume?
            </button>

            <button onClick={() => setQuestion("What skills do I have?")}>
                What skills do I have?
            </button>

            <button onClick={() => setQuestion("How should I prepare for placements?")}>
                How should I prepare for placements?
            </button>
        </div>
    </div>
)}

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
    <div className="message-label">
        {message.role === "user" ? "You" : "PlacementPrepAI"}
    </div>

    {message.role === "ai" ? (
        <ReactMarkdown>{message.text}</ReactMarkdown>
    ) : (
        <p>{message.text}</p>
    )}
</div>
                ))}

                {isLoading && (
    <div className="ai-message typing-indicator">
        <span></span>
        <span></span>
        <span></span>
    </div>
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