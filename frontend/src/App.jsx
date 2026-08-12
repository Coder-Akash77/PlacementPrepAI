import "./App.css";
import Navbar from "./components/Navbar";
import ChatBox from "./components/ChatBox";

function App() {
    return (
        <div>
            <Navbar />

            <main>
                <h1>Prepare Smarter. Get Placed Faster.</h1>
                <p>
                    Ask questions about your resume, skills, projects, education,
                    and placement preparation.
                </p>
                <button>Start Chat</button>
                <ChatBox />
            </main>
        </div>
    );
}

export default App;