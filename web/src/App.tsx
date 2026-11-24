import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, api } from "./api/client";
import { StatusStrip } from "./components/StatusStrip";
import { Activity } from "./pages/Activity";
import { Approvals } from "./pages/Approvals";
import { Backtest } from "./pages/Backtest";
import { Controls } from "./pages/Controls";
import { Overview } from "./pages/Overview";
import { Positions } from "./pages/Positions";
import { Reports } from "./pages/Reports";
import { Rules } from "./pages/Rules";
import { Shadow } from "./pages/Shadow";

const TABS = ["Overview", "Approvals", "Rules", "Shadow", "Positions", "Activity", "Reports", "Backtest", "Controls"] as const;
type Tab = (typeof TABS)[number];

function ComingSoon({ name }: { name: string }) {
  return <p className="muted">{name}: coming in spec 3.x.</p>;
}

function Login({ onSuccess }: { onSuccess: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const login = useMutation({
    mutationFn: api.login,
    onSuccess,
    onError: (e) =>
      setError(
        e instanceof ApiError && e.status === 401
          ? "Wrong password."
          : "Login failed; is the API up?",
      ),
  });
  return (
    <div className="login">
      <h1>Trader</h1>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          login.mutate(password);
        }}
      >
        <label htmlFor="password">Dashboard password</label>
        <input
          id="password"
          type="password"
          autoFocus
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <button type="submit" disabled={login.isPending || password === ""}>
          Log in
        </button>
        {error ? <p className="error">{error}</p> : null}
      </form>
    </div>
  );
}

export function App() {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<Tab>("Overview");
  const session = useQuery({
    queryKey: ["status"],
    queryFn: api.status,
    retry: false,
  });

  if (session.isLoading) return <p>Loading…</p>;
  if (session.isError) {
    const is401 = session.error instanceof ApiError && session.error.status === 401;
    if (!is401) return <p>Could not reach the API. Is it running?</p>;
    return <Login onSuccess={() => void queryClient.invalidateQueries()} />;
  }

  return (
    <div className="shell">
      <header className="shell__header">
        <h1>Trader</h1>
        <span className="muted">{session.data?.mode} mode</span>
      </header>
      <StatusStrip />
      <nav className="tabs">
        {TABS.map((t) => (
          <button
            key={t}
            className={`tab ${tab === t ? "tab--active" : ""}`}
            onClick={() => setTab(t)}
          >
            {t}
          </button>
        ))}
      </nav>
      <main>
        {tab === "Overview" ? (
          <Overview />
        ) : tab === "Approvals" ? (
          <Approvals />
        ) : tab === "Rules" ? (
          <Rules />
        ) : tab === "Shadow" ? (
          <Shadow />
        ) : tab === "Positions" ? (
          <Positions />
        ) : tab === "Activity" ? (
          <Activity />
        ) : tab === "Reports" ? (
          <Reports />
        ) : tab === "Backtest" ? (
          <Backtest />
        ) : tab === "Controls" ? (
          <Controls />
        ) : (
          <ComingSoon name={tab} />
        )}
      </main>
    </div>
  );
}
