import { useEffect, useState } from "react";
import { checkHealth } from "../services/api.js";

export default function Home() {
  const [status, setStatus] = useState("checking...");

  useEffect(() => {
    checkHealth()
      .then((data) => setStatus(data.status))
      .catch(() => setStatus("backend not reachable"));
  }, []);

  return (
    <main className="container">
      <h1>PlacementPrepAI</h1>
      <p>Resume-aware AI placement preparation.</p>
      <p className="status">
        Backend status: <strong>{status}</strong>
      </p>
    </main>
  );
}
