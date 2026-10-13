const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000/api";

export async function checkHealth() {
  const response = await fetch(`${API_URL}/health`);
  if (!response.ok) {
    throw new Error(`Backend returned ${response.status}`);
  }
  return response.json();
}
