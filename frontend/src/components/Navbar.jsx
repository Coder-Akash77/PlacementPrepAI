import "./Navbar.css";

function Navbar() {
    return (
        <nav>
            <div className="navbar-brand">
                <h2>PlacementPrepAI</h2>
                <span>AI Placement Assistant</span>
            </div>

            <div className="navbar-status">
                <span className="status-dot"></span>
                AI Ready
            </div>
        </nav>
    );
}

export default Navbar;