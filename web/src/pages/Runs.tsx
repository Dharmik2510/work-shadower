import { Fragment, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, ChevronDown, ChevronRight } from "lucide-react";
import { api, type Run, type RunStatus, type StepStatus } from "../api";
import { useUser } from "../auth/AuthContext";
import { Empty, ErrorNote, HealthBadge, Spinner } from "../components/bits";
import { Dot } from "../components/Dot";
import { dateTime, duration, relTime } from "../lib/format";
import { useAsync } from "../lib/useAsync";

const RUN_LABEL: Record<RunStatus, string> = { running: "Running", succeeded: "Succeeded", failed: "Failed", aborted: "Stopped" };
const STEP_LABEL: Record<StepStatus, string> = { ok: "Done", repaired: "Self-repaired", failed: "Failed", skipped: "Skipped", confirmed: "Confirmed by person" };
const STRATEGY_LABEL: Record<string, string> = { deterministic: "Replayed exactly", llm_repair: "AI repair", vision: "Screen match", human: "Person" };

export default function Runs() {
  const { id = "" } = useParams();
  const user = useUser();
  const skill = useAsync(() => api.getSkill(id), [id]);
  const runs = useAsync(() => api.listRuns(id, { limit: 50 }), [id]);
  const [open, setOpen] = useState<string | null>(null);
  const s = skill.data;
  const content = s?.published ?? s?.draft;
  const stepCount = content?.steps.length ?? 0;

  return (
    <div className="page">
      <Link to={`/skills/${id}`} className="back">
        <ArrowLeft size={16} aria-hidden /> {content?.title ?? "Skill"}
      </Link>
      <header className="pagehead pagehead--row">
        <div>
          <h1>Run history</h1>
          <p>Every time someone used “Do it for me”, and how each step went.</p>
        </div>
        {s && <HealthBadge health={s.health} />}
      </header>

      {(skill.error || runs.error) && <ErrorNote error={(skill.error || runs.error)!} onRetry={runs.reload} />}
      {runs.loading && !runs.data && <Spinner label="Loading runs" />}
      {runs.data && runs.data.items.length === 0 && (
        <Empty art={<Dot size={56} />} title="No runs yet">
          <p>When someone clicks “Do it for me on my Mac”, the run and each step's outcome appear here.</p>
        </Empty>
      )}

      {runs.data && runs.data.items.length > 0 && (
        <div className="tablewrap">
          <table className="table runs">
            <thead>
              <tr>
                <th scope="col">When</th>
                <th scope="col">Who</th>
                <th scope="col">Version</th>
                <th scope="col">Mode</th>
                <th scope="col">Steps</th>
                <th scope="col">Result</th>
                <th scope="col">
                  <span className="sr-only">Details</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {runs.data.items.map((r) => (
                <Fragment key={r.id}>
                  <tr className={open === r.id ? "is-open" : ""}>
                    <td data-label="When" title={dateTime(r.started_at)}>
                      {relTime(r.started_at)}
                    </td>
                    <td data-label="Who">{r.user ? (r.user.id === user.id ? "You" : r.user.name) : "–"}</td>
                    <td data-label="Version">v{r.version}</td>
                    <td data-label="Mode">{r.mode === "auto" ? "Automatic" : "Guided"}</td>
                    <td data-label="Steps">
                      <StepStrip run={r} total={stepCount} />
                    </td>
                    <td data-label="Result">
                      <span className={`pill pill--run-${r.status}`}>{RUN_LABEL[r.status]}</span>
                    </td>
                    <td className="runs__toggle">
                      <button className="icon-btn icon-btn--sm" aria-expanded={open === r.id} aria-label="Show step details" onClick={() => setOpen(open === r.id ? null : r.id)}>
                        {open === r.id ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                      </button>
                    </td>
                  </tr>
                  {open === r.id && (
                    <tr className="runs__detail">
                      <td colSpan={7}>
                        {r.error && <p className="rec__err">{r.error}</p>}
                        <ol className="runsteps">
                          {(r.steps ?? []).map((st) => (
                            <li key={st.step_index} className={`runstep runstep--${st.status}`}>
                              <span className="step__n step__n--sm">{st.step_index}</span>
                              <span className="runstep__title">{content?.steps[st.step_index - 1]?.title ?? `Step ${st.step_index}`}</span>
                              <span className={`pill pill--step-${st.status}`}>{STEP_LABEL[st.status]}</span>
                              <span className="runstep__how">{STRATEGY_LABEL[st.strategy] ?? st.strategy}</span>
                              <span className="runstep__dur">{duration(st.duration_ms)}</span>
                              {st.detail && <span className="runstep__detail">{st.detail}</span>}
                            </li>
                          ))}
                          {(r.steps?.length ?? 0) === 0 && <li className="muted">No steps reported.</li>}
                        </ol>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function StepStrip({ run, total }: { run: Run; total: number }) {
  const byIdx = new Map((run.steps ?? []).map((s) => [s.step_index, s]));
  const n = Math.max(total, ...(run.steps ?? []).map((s) => s.step_index), 0);
  return (
    <span className="strip" aria-label={`${run.steps?.length ?? 0} of ${n} steps reported`}>
      {Array.from({ length: n }, (_, i) => {
        const st = byIdx.get(i + 1);
        return <span key={i} className={`strip__cell strip__cell--${st?.status ?? "none"}`} title={`Step ${i + 1}: ${st ? STEP_LABEL[st.status] : "Not reached"}`} />;
      })}
    </span>
  );
}
