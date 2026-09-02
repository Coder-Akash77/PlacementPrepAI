import { useState } from "react";
import "./App.css";
import Navbar from "./components/Navbar";
import ChatBox from "./components/ChatBox";

function App() {
    const [showChat, setShowChat] = useState(false);

    return (
        <div>
            <Navbar />

            <main>
                {!showChat ? (
                    <>
                        <div className="hero-badge">
                            AI-Powered Placement Assistant
                        </div>
                        <h1>Prepare Smarter. Get Placed Faster.</h1>

                        <p>
                            Ask questions about your resume, skills, projects,
                            education, and placement preparation.
                        </p>

                        <button onClick={() => setShowChat(true)}>
                            Start Chat →
                        </button>
                    </>
                ) : (
                    <ChatBox />
                )}
            </main>
        </div>
    );
}

export default App;