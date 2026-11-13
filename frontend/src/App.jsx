import { useCallback, useEffect, useState } from "react";
import Login from "./components/Login.jsx";
import Home from "./pages/Home.jsx";
import {
  AUTH_EXPIRED_EVENT,
  getAuthToken,
  getCurrentUser,
  setAuthToken,
} from "./services/api.js";

export default function App() {
  const [user, setUser] = useState(null);
  const [checkingSession, setCheckingSession] = useState(Boolean(getAuthToken()));

  useEffect(() => {
    if (!getAuthToken()) return;
    getCurrentUser()
      .then((result) => setUser(result.user))
      .catch(() => {})
      .finally(() => setCheckingSession(false));
  }, []);

  useEffect(() => {
    const handleExpired = () => setUser(null);
    window.addEventListener(AUTH_EXPIRED_EVENT, handleExpired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, handleExpired);
  }, []);

  const handleSignOut = useCallback(() => {
    setAuthToken("");
    window.google?.accounts?.id?.disableAutoSelect();
    setUser(null);
  }, []);

  if (checkingSession) {
    return (
      <main className="app-shell">
        <p className="loading-note" role="status">Signing you in…</p>
      </main>
    );
  }
  if (!user) return <Login onSignedIn={setUser} />;
  return <Home user={user} onSignOut={handleSignOut} />;
}
