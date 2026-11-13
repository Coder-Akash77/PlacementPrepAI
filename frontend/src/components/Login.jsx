import { useEffect, useRef, useState } from "react";
import { loginWithGoogle, setAuthToken } from "../services/api.js";

const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID || "";
const GOOGLE_SCRIPT_SRC = "https://accounts.google.com/gsi/client";

function loadGoogleScript() {
  if (window.google?.accounts?.id) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const existing = document.querySelector(`script[src="${GOOGLE_SCRIPT_SRC}"]`);
    const script = existing || document.createElement("script");
    script.addEventListener("load", resolve, { once: true });
    script.addEventListener(
      "error",
      () => reject(new Error("Could not load Google sign-in.")),
      { once: true }
    );
    if (!existing) {
      script.src = GOOGLE_SCRIPT_SRC;
      script.async = true;
      document.head.appendChild(script);
    }
  });
}

export default function Login({ onSignedIn }) {
  const buttonRef = useRef(null);
  const [error, setError] = useState(
    GOOGLE_CLIENT_ID ? "" : "Google sign-in is not configured (missing VITE_GOOGLE_CLIENT_ID)."
  );

  useEffect(() => {
    if (!GOOGLE_CLIENT_ID) return undefined;
    let cancelled = false;

    loadGoogleScript()
      .then(() => {
        if (cancelled || !buttonRef.current) return;
        window.google.accounts.id.initialize({
          client_id: GOOGLE_CLIENT_ID,
          callback: async ({ credential }) => {
            setError("");
            try {
              const result = await loginWithGoogle(credential);
              setAuthToken(result.token);
              onSignedIn(result.user);
            } catch (loginError) {
              setError(loginError.message);
            }
          },
        });
        window.google.accounts.id.renderButton(buttonRef.current, {
          theme: "outline",
          size: "large",
          text: "signin_with",
          shape: "pill",
        });
      })
      .catch((loadError) => {
        if (!cancelled) setError(loadError.message);
      });

    return () => {
      cancelled = true;
    };
  }, [onSignedIn]);

  return (
    <main className="app-shell">
      <section className="hero login-card">
        <p className="eyebrow">WELCOME TO PLACEMENTPREPAI</p>
        <h1>Sign in to continue.</h1>
        <p className="hero-copy">
          Your practice and interview history is saved to your account, so you
          can pick up where you left off on any device.
        </p>
        <div ref={buttonRef} className="google-button" />
        {error && (
          <p className="error-message" role="alert">
            {error}
          </p>
        )}
      </section>
    </main>
  );
}
