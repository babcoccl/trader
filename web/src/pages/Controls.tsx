import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, api } from "../api/client";

interface EditableSpec {
  key: string;
  label: string;
  input: "text" | "number" | "select";
  options?: { value: string; label: string }[];
  parse?: (raw: string) => unknown;
  format?: (value: unknown) => string;
}

const EDITABLE: EditableSpec[] = [
  {
    key: "silence_alarm_minutes",
    label: "Silence alarm",
    input: "number",
    parse: (raw) => Number.parseInt(raw, 10),
    format: (v) => `${v} minutes`,
  },
  {
    key: "digest_time_utc",
    label: "Daily digest",
    input: "text",
    format: (v) => `${v} UTC`,
  },
];

function SettingRow({ spec, value }: { spec: EditableSpec; value: unknown }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [raw, setRaw] = useState("");
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: (v: unknown) => api.putSetting(spec.key, v),
    onSuccess: () => {
      setEditing(false);
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
    },
    onError: (e) =>
      setError(e instanceof ApiError ? `Rejected (${e.status}): ${e.message}` : "Save failed."),
  });

  const display = spec.format ? spec.format(value) : String(value);

  return (
    <tr>
      <td className="muted">{spec.label}</td>
      <td>
        {editing ? (
          <form
            className="setting-edit"
            onSubmit={(e) => {
              e.preventDefault();
              save.mutate(spec.parse ? spec.parse(raw) : raw);
            }}
          >
            {spec.input === "select" ? (
              <select
                aria-label={`${spec.label} value`}
                value={raw}
                onChange={(e) => setRaw(e.target.value)}
              >
                {(spec.options ?? []).map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            ) : (
              <input
                aria-label={`${spec.label} value`}
                type={spec.input}
                step="any"
                value={raw}
                onChange={(e) => setRaw(e.target.value)}
              />
            )}
            <button type="submit" disabled={save.isPending}>
              Save
            </button>
            <button type="button" onClick={() => setEditing(false)}>
              Cancel
            </button>
            {error ? <span className="error">{error}</span> : null}
          </form>
        ) : (
          <>
            {display}{" "}
            <button
              type="button"
              className="link"
              onClick={() => {
                setRaw(
                  spec.input === "select"
                    ? (spec.options?.[0]?.value ?? String(value))
                    : String(value),
                );
                setEditing(true);
                setError(null);
              }}
            >
              Edit
            </button>
          </>
        )}
      </td>
    </tr>
  );
}

export function Controls() {
  const queryClient = useQueryClient();
  const [flattenOpen, setFlattenOpen] = useState(false);
  const [confirm, setConfirm] = useState("");
  const [flattenDone, setFlattenDone] = useState(false);

  const status = useQuery({
    queryKey: ["status"],
    queryFn: api.status,
    refetchInterval: 30_000,
  });
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });

  const halt = useMutation({
    mutationFn: api.halt,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["status"] }),
  });
  const resume = useMutation({
    mutationFn: api.resume,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["status"] }),
  });
  const flatten = useMutation({
    mutationFn: api.flatten,
    onSuccess: () => {
      setFlattenOpen(false);
      setConfirm("");
      setFlattenDone(true);
      void queryClient.invalidateQueries({ queryKey: ["status"] });
    },
  });

  if (status.isLoading || settings.isLoading) return <p>Loading…</p>;
  if (status.isError || !status.data) return <p>Could not load status.</p>;
  const s = status.data;

  const settingsByKey = new Map((settings.data ?? []).map((r) => [r.key, r.value]));
  const readOnly: [string, string][] = [
    ["Autonomy level", "Approve every plan (other levels are not built yet)"],
    ["Approval expiry", "6 hours (fixed)"],
    ["Drawdown halt", `${(s.halt_at * 100).toFixed(0)}% from peak`],
    ["Sizing", "Volatility target 40%, assets set by TRADER_SYMBOLS (default BTC and ETH), long only, no leverage"],
    ["Venue and keys", "Bitstamp. API key without withdrawal permission."],
    ["Mode", s.mode],
  ];

  return (
    <div className="page">
      <div className="card">
        <div className="card__title">
          Emergency controls{" "}
          <span className={`badge ${s.halted ? "badge--red" : "badge--green"}`}>
            {s.halted ? "Halted" : "Running"}
          </span>
        </div>
        <div className="controls-row">
          {s.halted ? (
            <button
              type="button"
              onClick={() => resume.mutate()}
              disabled={resume.isPending}
            >
              Resume trading
            </button>
          ) : (
            <button
              type="button"
              className="btn--warning"
              onClick={() => halt.mutate()}
              disabled={halt.isPending}
            >
              Halt: cancel orders, stop new plans
            </button>
          )}
          <button
            type="button"
            className="btn--danger"
            onClick={() => setFlattenOpen(true)}
          >
            Flatten: sell everything to cash
          </button>
        </div>
        {flattenDone ? (
          <p className="muted">Flatten requested; the executor sells at market.</p>
        ) : null}
        {halt.isError || resume.isError ? (
          <p className="error">Control request failed.</p>
        ) : null}
        {s.halted && s.halt_reason ? <p className="muted">Reason: {s.halt_reason}</p> : null}
        <p className="muted">
          Halt is one click. Flatten asks you to type FLATTEN first. The halt also triggers
          automatically at a 35% drop from the peak.
        </p>
      </div>

      {flattenOpen ? (
        <div className="dialog-backdrop" role="dialog" aria-label="Confirm flatten">
          <div className="dialog">
            <div className="card__title">Flatten everything?</div>
            <p>
              This sells every position to cash at market prices. Type FLATTEN to confirm.
            </p>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                flatten.mutate(confirm);
              }}
            >
              <input
                aria-label="Type FLATTEN to confirm"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                autoFocus
              />
              <div className="controls-row">
                <button
                  type="submit"
                  className="btn--danger"
                  disabled={confirm !== "FLATTEN" || flatten.isPending}
                >
                  Flatten now
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setFlattenOpen(false);
                    setConfirm("");
                  }}
                >
                  Cancel
                </button>
              </div>
              {flatten.isError ? (
                <p className="error">Flatten request failed.</p>
              ) : null}
            </form>
          </div>
        </div>
      ) : null}

      <div className="card">
        <div className="card__title">Limits and behaviour</div>
        {settings.isError ? (
          <p className="muted">Could not load settings.</p>
        ) : (
          <table className="kv-table">
            <tbody>
              {EDITABLE.map((spec) => (
                <SettingRow
                  key={spec.key}
                  spec={spec}
                  value={settingsByKey.get(spec.key)}
                />
              ))}
              {readOnly.map(([label, value]) => (
                <tr key={label}>
                  <td className="muted">{label}</td>
                  <td>{value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="muted">
          The few settings that carry risk, each with its current value; every change is
          logged.
        </p>
      </div>
    </div>
  );
}
