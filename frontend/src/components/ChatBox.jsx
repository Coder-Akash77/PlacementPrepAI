import { useState } from "react";
import "./ChatBox.css";
function ChatBox() {
    const [question, setQuestion] = useState("");
    const [messages, setMessages] = useState([]);

    const handleSend = () => {
        if (question.trim() === "") {
            return;
        }

        setMessages([
            ...messages,
            {
                role: "user",
                text: question
            }
        ]);

        setQuestion("");
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
                />

                <button onClick={handleSend}>Send</button>
            </div>

            <div className="messages">
                {messages.map((message, index) => (
                    <p
                        key={index}
                        className={message.role === "user" ? "user-message" : "ai-message"}
                    >
                        {message.text}
                    </p>
                ))}
            </div>

        </section>
    );
}

export default ChatBox;