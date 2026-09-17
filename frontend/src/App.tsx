import { useCallback, useEffect, useState } from "react";

type ConnectionState = "checking" | "connected" | "failed";

interface HealthResponse {
  status: "ok";
  service: string;
  timestamp: string;
}

const labels: Record<ConnectionState, string> = {
  checking: "確認中",
  connected: "接続済み",
  failed: "接続できません"
};

export function App() {
  const [state, setState] = useState<ConnectionState>("checking");
  const [health, setHealth] = useState<HealthResponse | null>(null);

  const checkHealth = useCallback(async () => {
    setState("checking");
    try {
      const response = await fetch("/api/health");
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = (await response.json()) as HealthResponse;
      setHealth(data);
      setState("connected");
    } catch {
      setHealth(null);
      setState("failed");
    }
  }, []);

  useEffect(() => {
    void checkHealth();
  }, [checkHealth]);

  return (
    <main className="shell">
      <section className="hero">
        <p className="eyebrow">LOCAL WORKFLOW CONSOLE</p>
        <h1>LoRA Maker</h1>
        <p className="lead">LoRA制作工程を、安全にひとつの場所へ。</p>
      </section>

      <section className="status-card" aria-live="polite">
        <div>
          <span className={`indicator indicator--${state}`} aria-hidden="true" />
          <span className="status-label">FastAPI</span>
        </div>
        <strong>{labels[state]}</strong>
        {health && <small>{health.service}</small>}
        <button type="button" onClick={() => void checkHealth()} disabled={state === "checking"}>
          再確認
        </button>
      </section>

      <p className="phase-note">開発基盤を準備しました。工程機能は次のフェーズから追加されます。</p>
    </main>
  );
}
