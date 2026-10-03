import { useState } from "react";
import { RotateCcw } from "lucide-react";
import { api, type Flags, type TeamUsage } from "../api";
import { Empty, ErrorNote, Spinner } from "../components/bits";
import { useToast } from "../components/Toast";
import { dateTime, num, pct, relTime, usd } from "../lib/format";
import { useAsync } from "../lib/useAsync";

export default function Admin() {
  return (
    <div className="page admin">
      <header className="pagehead">
        <h1>Admin</h1>
        <p>Pause recording or replay for everyone, keep an eye on AI spend, and restart stuck jobs.</p>
      </header>
      <KillSwitches />
      <UsagePanel />
      <DeadJobs />
    </div>
  );
}

function KillSwitches() {
  const flags = useAsync(() => api.getAdminFlags(), []);
  const toast = useToast();
  const [saving, setSaving] = useState<keyof Flags | null>(null);
  const [confirm, setConfirm] = useState<keyof Flags | null>(null);

  async function update(k: keyof Flags, v: Flags[keyof Flags]) {
    if (!flags.data) return;
    setSaving(k);
    try {
      const next = await api.putAdminFlags({ ...flags.data, [k]: v });
      flags.setData(next);
      toast(SWITCHES.find((s) => s.key === k)?.[v ? "onToast" : "offToast"] ?? "Settings saved", "good");
    } catch (e) {
      toast((e as Error).message, "bad");
    } finally {
      setSaving(null);
      setConfirm(null);
    }
  }

  return (
    <section className="panel">
      <h2>Kill switches</h2>
      {flags.error && <ErrorNote error={flags.error} onRetry={flags.reload} />}
      {!flags.data && flags.loading && <Spinner />}
      {flags.data && (
        <div className="switches">
          {SWITCHES.map((s) => {
            const on = !!flags.data![s.key];
            return (
              <div key={s.key} className={`switch ${on ? "" : "switch--off"}`}>
                <div className="switch__text">
                  <h3>{s.label}</h3>
                  <p>{on ? s.onHelp : s.offHelp}</p>
                </div>
                {confirm === s.key ? (
                  <div className="switch__confirm">
                    <button className="btn btn--danger btn--sm" disabled={saving === s.key} onClick={() => update(s.key, false)}>
                      {saving === s.key ? "Pausing…" : `Pause ${s.short}`}
                    </button>
                    <button className="btn btn--ghost btn--sm" onClick={() => setConfirm(null)}>
                      Cancel
                    </button>
                  </div>
                ) : (
                  <label className={`toggle toggle--lg ${on ? "is-on" : ""}`}>
                    <input
                      type="checkbox"
                      checked={on}
                      disabled={saving === s.key}
                      onChange={(e) => (e.target.checked ? update(s.key, true) : setConfirm(s.key))}
                    />
                    <span className="toggle__track" aria-hidden />
                    <span>{on ? "On" : "Paused"}</span>
                  </label>
                )}
              </div>
            );
          })}
          <div className="switch switch--meta">
            <div className="switch__text">
              <h3>Recording limits</h3>
              <p>
                Up to {flags.data.max_recording_minutes} minutes per recording. Screenshots: {flags.data.screenshot_policy === "none" ? "never captured" : "key moments only"}.
              </p>
            </div>
            <label className="select">
              <span className="sr-only">Screenshot policy</span>
              <select value={flags.data.screenshot_policy} onChange={(e) => update("screenshot_policy", e.target.value as Flags["screenshot_policy"])}>
                <option value="key_moments">Key moments</option>
                <option value="none">No screenshots</option>
              </select>
            </label>
          </div>
        </div>
      )}
    </section>
  );
}

const SWITCHES = [
  {
    key: "recording_enabled" as const,
    label: "Recording",
    short: "recording",
    onHelp: "People can record new workflows with the dot.",
    offHelp: "Paused. The dot won't start new recordings on any Mac.",
    onToast: "Recording turned back on",
    offToast: "Recording paused for everyone",
  },
  {
    key: "replay_enabled" as const,
    label: "Do it for me",
    short: "replay",
    onHelp: "The Mac app can run published skills.",
    offHelp: "Paused. People can still read skills and follow the steps themselves.",
    onToast: "Replay turned back on",
    offToast: "Replay paused for everyone",
  },
  {
    key: "llm_enabled" as const,
    label: "AI drafting and repair",
    short: "AI",
    onHelp: "Drafts and step repairs use the language model.",
    offHelp: "Paused. Drafts use the built-in rules and broken steps stop and ask the person.",
    onToast: "AI turned back on",
    offToast: "AI paused. Rule-based drafting is in use",
  },
];

function teamLabel(t: TeamUsage): string {
  if (t.team_name) return t.team_name;
  if (typeof t.team === "string") return t.team;
  if (t.team && typeof t.team === "object") return t.team.name;
  return "No team";
}

function UsagePanel() {
  const [days, setDays] = useState(30);
  const usage = useAsync(() => api.usage(days), [days]);
  const u = usage.data;
  return (
    <section className="panel">
      <div className="panel__head">
        <h2>Usage</h2>
        <div className="seg" role="radiogroup" aria-label="Time range">
          {[7, 30, 90].map((d) => (
            <button key={d} role="radio" aria-checked={days === d} className={days === d ? "is-on" : ""} onClick={() => setDays(d)}>
              {d} days
            </button>
          ))}
        </div>
      </div>
      {usage.error && <ErrorNote error={usage.error} onRetry={usage.reload} />}
      {!u && usage.loading && <Spinner />}
      {u && (
        <div className={usage.loading ? "is-stale" : ""}>
          <div className="tiles">
            <Tile label="Estimated AI cost" value={usd(u.est_cost_usd)} sub={`${num(u.llm_calls)} model calls`} lead />
            <Tile label="Tokens in" value={num(u.input_tokens)} sub={`${num(u.output_tokens)} out`} />
            <Tile label="Skills created" value={num(u.skills_created)} />
            <Tile label="Runs" value={num(u.runs)} sub={`${pct(u.run_success_rate)} succeeded`} />
          </div>
          <TeamBars data={u.by_team} />
        </div>
      )}
    </section>
  );
}

function Tile({ label, value, sub, lead }: { label: string; value: string; sub?: string; lead?: boolean }) {
  return (
    <div className={`tile ${lead ? "tile--lead" : ""}`}>
      <p className="tile__label">{label}</p>
      <p className="tile__value">{value}</p>
      {sub && <p className="tile__sub">{sub}</p>}
    </div>
  );
}

/** Horizontal bars of estimated cost by team; one series, so no legend. */
function TeamBars({ data }: { data: TeamUsage[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const rows = [...data].sort((a, b) => (b.est_cost_usd ?? 0) - (a.est_cost_usd ?? 0));
  if (!rows.length) return <p className="muted">No AI usage by team in this period.</p>;
  const metric = rows.some((r) => r.est_cost_usd !== undefined) ? "cost" : "calls";
  const val = (r: TeamUsage) => (metric === "cost" ? r.est_cost_usd ?? 0 : r.llm_calls ?? 0);
  const max = Math.max(...rows.map(val), 0.0001);
  const rowH = 36,
    labelW = 150,
    valueW = 120,
    W = 640;
  const barW = W - labelW - valueW;
  return (
    <figure className="bars">
      <figcaption>{metric === "cost" ? "Estimated AI cost by team" : "Model calls by team"}</figcaption>
      <svg viewBox={`0 0 ${W} ${rows.length * rowH}`} role="img" aria-label="Bar chart of AI usage by team" preserveAspectRatio="xMinYMin meet">
        {rows.map((r, i) => {
          const w = Math.max(3, (val(r) / max) * barW);
          const y = i * rowH;
          return (
            <g key={i} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} className={hover === i ? "is-hover" : ""}>
              <rect x="0" y={y} width={W} height={rowH} fill="transparent" />
              <text x="0" y={y + rowH / 2 + 5} className="bars__label">
                {teamLabel(r)}
              </text>
              <rect x={labelW} y={y + 10} width={barW} height={rowH - 20} rx="4" className="bars__track" />
              <rect x={labelW} y={y + 10} width={w} height={rowH - 20} rx="4" className="bars__bar" />
              <text x={labelW + w + 8} y={y + rowH / 2 + 5} className="bars__value">
                {metric === "cost" ? usd(val(r)) : num(val(r))}
                {hover === i && r.llm_calls !== undefined && metric === "cost" ? `  ${num(r.llm_calls)} calls` : ""}
              </text>
            </g>
          );
        })}
      </svg>
      <table className="sr-only">
        <caption>AI usage by team</caption>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <th scope="row">{teamLabel(r)}</th>
              <td>{usd(r.est_cost_usd)}</td>
              <td>{num(r.llm_calls)} calls</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}

function DeadJobs() {
  const jobs = useAsync(() => api.deadJobs(), []);
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  async function retry(id: string) {
    setBusy(id);
    try {
      await api.retryJob(id);
      jobs.setData((d) => ({ items: (d?.items ?? []).filter((j) => j.id !== id) }));
      toast("Job queued to retry", "good");
    } catch (e) {
      toast((e as Error).message, "bad");
    } finally {
      setBusy(null);
    }
  }
  return (
    <section className="panel">
      <h2>Stuck jobs</h2>
      <p className="panel__lede">Background jobs that failed every retry. Retrying puts them back in the queue.</p>
      {jobs.error && <ErrorNote error={jobs.error} onRetry={jobs.reload} />}
      {!jobs.data && jobs.loading && <Spinner />}
      {jobs.data && jobs.data.items.length === 0 && <Empty title="No stuck jobs">Everything in the queue is running normally.</Empty>}
      {jobs.data && jobs.data.items.length > 0 && (
        <div className="tablewrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Job</th>
                <th scope="col">Last error</th>
                <th scope="col">Attempts</th>
                <th scope="col">Failed</th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {jobs.data.items.map((j) => (
                <tr key={j.id}>
                  <td data-label="Job">
                    <strong>{(j.kind ?? j.type ?? "job").replace(/_/g, " ")}</strong>
                    <span className="muted tiny">{j.id}</span>
                  </td>
                  <td data-label="Last error" className="errcell">
                    {j.last_error ?? j.error ?? "–"}
                  </td>
                  <td data-label="Attempts">
                    {j.attempts ?? "–"}
                    {j.max_attempts ? ` of ${j.max_attempts}` : ""}
                  </td>
                  <td data-label="Failed" title={dateTime(j.updated_at)}>
                    {relTime(j.updated_at ?? j.created_at)}
                  </td>
                  <td>
                    <button className="btn btn--quiet btn--sm" disabled={busy === j.id} onClick={() => retry(j.id)}>
                      <RotateCcw size={15} aria-hidden /> {busy === j.id ? "Retrying…" : "Retry"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
