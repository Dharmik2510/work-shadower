import { useEffect } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle } from "lucide-react";
import { api } from "../api";
import { Dot } from "../components/Dot";
import { Empty, ErrorNote, RecordingStatusPill, RecordWithDotSteps, Spinner } from "../components/bits";
import { dateTime, relTime } from "../lib/format";
import { useAsync } from "../lib/useAsync";

const POLL_MS = 3000;

export default function Recordings() {
  const recs = useAsync(() => api.listRecordings({ limit: 50 }), []);
  const items = recs.data?.items ?? [];
  const pending = items.some((r) => r.status === "received" || r.status === "processing");

  // Poll while anything is still being turned into a draft.
  useEffect(() => {
    if (!pending) return;
    const t = setInterval(() => {
      api.listRecordings({ limit: 50 }).then(recs.setData, () => {});
    }, POLL_MS);
    return () => clearInterval(t);
  }, [pending, recs.setData]);

  return (
    <div className="page">
      <header className="pagehead">
        <h1>My recordings</h1>
        <p>Everything you've recorded with the dot. Each one becomes a draft only you can see until you publish it.</p>
      </header>

      {recs.error && <ErrorNote error={recs.error} onRetry={recs.reload} />}
      {!recs.data && recs.loading && <Spinner label="Loading recordings" />}

      {recs.data && items.length === 0 && (
        <Empty art={<Dot size={56} />} title="No recordings yet">
          <RecordWithDotSteps />
        </Empty>
      )}

      {items.length > 0 && (
        <>
          {pending && (
            <p className="polling" role="status">
              <Dot size={18} awake /> Writing drafts. This page updates on its own.
            </p>
          )}
          <ul className="recs">
            {items.map((r) => (
              <li key={r.id} className={`rec rec--${r.status}`}>
                <div className="rec__main">
                  <h3>{r.title_hint || "Untitled recording"}</h3>
                  <p className="rec__meta">
                    <span title={dateTime(r.created_at)}>Recorded {relTime(r.created_at)}</span>
                    <span>{r.event_count.toLocaleString()} actions</span>
                  </p>
                  {r.status === "failed" && r.error && (
                    <p className="rec__err">
                      <AlertTriangle size={15} aria-hidden /> {r.error}
                    </p>
                  )}
                </div>
                <RecordingStatusPill status={r.status} />
                <div className="rec__act">
                  {r.status === "ready" && r.skill_id ? (
                    <Link className="btn btn--quiet btn--sm" to={`/skills/${r.skill_id}/edit`}>
                      Review draft
                    </Link>
                  ) : r.status === "failed" ? (
                    <span className="muted">Record it again</span>
                  ) : (
                    <span className="muted">Usually under a minute</span>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
