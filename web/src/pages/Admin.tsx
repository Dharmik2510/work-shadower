import { useState } from "react";
import { Download, RotateCcw } from "lucide-react";
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
        <p>Pause recording or replay for everyone, tune how drafts leave out unrelated steps, keep an eye on AI spend, and restart stuck jobs.</p>
      </header>
      <KillSwitches />
      <FilterPanel />
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
      const sw = SWITCHES.find((s) => s.key === k);
      toast(sw ? sw[v ? "onToast" : "offToast"] : "Settings saved", "good");
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
  {
    key: "filter_enabled" as const,
    label: "Leave out unrelated steps",
    short: "the step filter",
    onHelp: "Drafts grey out steps that don't look like part of the task (side trips, undone mistakes). Reviewers can put them back.",
    offHelp: "Paused. Drafts include every recorded step.",
    onToast: "Step filter turned back on",
    offToast: "Step filter paused. Drafts keep every step",
  },
  {
    key: "split_tasks_enabled" as const,
    label: "Split recordings into tasks",
    short: "task splitting",
    onHelp: "A recording that covers two unrelated tasks becomes two drafts.",
    offHelp: "Paused. Every recording becomes one draft.",
    onToast: "Task splitting turned back on",
    offToast: "Task splitting paused",
  },
];

const REASON_LABEL: Record<string, string> = {
  detour: "Side trip to another app",
  exploration: "Opened and closed",
  mistake_undone: "Undone right after",
  duplicate: "Repeated step",
  idle_or_noise: "Accidental input",
  unclear_relevance: "Unclear",
};

function FilterPanel() {
  const [days, setDays] = useState(30);
  const stats = useAsync(() => api.filterStats(days), [days]);
  const flags = useAsync(() => api.getAdminFlags(), []);
  const toast = useToast();
  const [drop, setDrop] = useState<string | null>(null);
  const [review, setReview] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [exporting, setExporting] = useState(false);
  const f = flags.data;
  const dropV = drop ?? (f ? String(f.filter_drop_threshold) : "");
  const reviewV = review ?? (f ? String(f.filter_review_threshold) : "");
  const dn = Number(dropV),
    rn = Number(reviewV);
  const valid = dn > 0 && dn <= 1 && rn > 0 && rn <= 1 && rn <= dn;
  const changed = !!f && (dn !== f.filter_drop_threshold || rn !== f.filter_review_threshold);

  async function saveThresholds() {
    if (!f || !valid) return;
    setSaving(true);
    try {
      flags.setData(await api.putAdminFlags({ ...f, filter_drop_threshold: dn, filter_review_threshold: rn }));
      setDrop(null);
      setReview(null);
      toast("Filter cut-offs saved. New recordings use them.", "good");
    } catch (e) {
      toast((e as Error).message, "bad");
    } finally {
      setSaving(false);
    }
  }

  async function download() {
    setExporting(true);
    try {
      const blob = await api.filterExport(90);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "filter-labels.ndjson";
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      toast((e as Error).message, "bad");
    } finally {
      setExporting(false);
    }
  }

  const st = stats.data;
  return (
    <section className="panel">
      <div className="panel__head">
        <h2>Step filter</h2>
        <div className="seg" role="radiogroup" aria-label="Time range">
          {[7, 30, 90].map((d) => (
            <button key={d} role="radio" aria-checked={days === d} className={days === d ? "is-on" : ""} onClick={() => setDays(d)}>
              {d} days
            </button>
          ))}
        </div>
      </div>
      <p className="panel__lede">
        Every recorded action gets a score for how likely it is <em>not</em> part of the task. At or above the leave-out cut-off it's greyed out in the
        draft; at or above the flag cut-off it's kept but flagged for the reviewer. Publishing tells us what reviewers actually kept.
      </p>
      {flags.error && <ErrorNote error={flags.error} onRetry={flags.reload} />}
      {f && (
        <div className="thresholds">
          <label className="field field--inline field--sm">
            <span>Leave out at</span>
            <input type="number" min={0.05} max={1} step={0.05} value={dropV} onChange={(e) => setDrop(e.target.value)} aria-invalid={!valid} />
          </label>
          <label className="field field--inline field--sm">
            <span>Flag at</span>
            <input type="number" min={0.05} max={1} step={0.05} value={reviewV} onChange={(e) => setReview(e.target.value)} aria-invalid={!valid} />
          </label>
          <button className="btn btn--quiet btn--sm" disabled={!changed || !valid || saving} onClick={saveThresholds}>
            {saving ? "Saving…" : "Save cut-offs"}
          </button>
          {!valid && <span className="muted tiny">Both between 0 and 1, and flag ≤ leave out.</span>}
        </div>
      )}
      {stats.error && <ErrorNote error={stats.error} onRetry={stats.reload} />}
      {!st && stats.loading && <Spinner />}
      {st && (
        <div className={stats.loading ? "is-stale" : ""}>
          <div className="tiles">
            <Tile label="Left out" value={num(st.decisions.drop)} sub={`${num(st.decisions.review)} flagged, of ${num(st.events)} actions`} lead />
            <Tile label="Put back by reviewers" value={pct(st.wrongly_dropped_rate)} sub="of left-out steps that were reviewed" />
            <Tile label="Missed" value={pct(st.missed_rate)} sub="kept steps reviewers removed" />
            <Tile label={st.provider === "jev" ? "Jev cost" : "Filter"} value={st.provider === "jev" ? usd(st.jev.est_cost_usd) : "Rules only"} sub={st.provider === "jev" ? `${num(st.jev.recordings)} recordings · ${num(st.jev.partial_failures)} with fallbacks` : "Set FILTER_PROVIDER=jev to add the model"} />
          </div>
          {st.reviewed === 0 ? (
            <p className="muted">No reviewed drafts yet. Numbers appear once people publish skills from recordings.</p>
          ) : (
            <div className="filter-tables">
              <div className="tablewrap">
                <table className="table table--compact">
                  <caption>If the leave-out cut-off were…</caption>
                  <thead>
                    <tr>
                      <th scope="col">Cut-off</th>
                      <th scope="col">Left out</th>
                      <th scope="col" title="Of the steps it would leave out, how many reviewers also removed">Right</th>
                      <th scope="col" title="Of the steps reviewers removed, how many it would have caught">Caught</th>
                    </tr>
                  </thead>
                  <tbody>
                    {st.threshold_curve.map((r) => (
                      <tr key={r.threshold} className={f && r.threshold === f.filter_drop_threshold ? "is-current" : ""}>
                        <td data-label="Cut-off">{r.threshold.toFixed(2)}</td>
                        <td data-label="Left out">{num(r.flagged)}</td>
                        <td data-label="Right">{pct(r.precision)}</td>
                        <td data-label="Caught">{pct(r.recall)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="tablewrap">
                <table className="table table--compact">
                  <caption>Why steps were left out or flagged</caption>
                  <thead>
                    <tr>
                      <th scope="col">Reason</th>
                      <th scope="col">Steps</th>
                      <th scope="col">Put back</th>
                    </tr>
                  </thead>
                  <tbody>
                    {st.by_reason.map((r) => (
                      <tr key={r.reason}>
                        <td data-label="Reason">{REASON_LABEL[r.reason] ?? r.reason}</td>
                        <td data-label="Steps">{num(r.n)}</td>
                        <td data-label="Put back">{num(r.restored)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          <button className="btn btn--ghost btn--sm" onClick={download} disabled={exporting}>
            <Download size={15} aria-hidden /> {exporting ? "Preparing…" : "Download labelled examples (90 days)"}
          </button>
        </div>
      )}
    </section>
  );
}

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
