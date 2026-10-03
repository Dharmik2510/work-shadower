import type { ReactNode } from "react";
import { AlertTriangle, CircleCheck, CircleDashed, CircleX, Loader2 } from "lucide-react";
import type { RecordingStatus, SkillHealth, SkillStatus, Visibility } from "../api";
import { pct, plural } from "../lib/format";

export function HealthBadge({ health, compact = false }: { health: SkillHealth; compact?: boolean }) {
  if (!health.runs || health.success_rate === null)
    return (
      <span className="health health--none" title="Nobody has run this with the Mac app yet">
        <CircleDashed size={14} aria-hidden />
        {compact ? "No runs" : "No runs yet"}
      </span>
    );
  const r = health.success_rate;
  const tone = r >= 0.9 ? "good" : r >= 0.7 ? "warn" : "bad";
  const Icon = tone === "good" ? CircleCheck : tone === "warn" ? AlertTriangle : CircleX;
  return (
    <span className={`health health--${tone}`} title={`${pct(r)} of ${plural(health.runs, "run")} finished without help`}>
      <Icon size={14} aria-hidden />
      <strong>{pct(r)}</strong>
      <span className="health__runs">{compact ? `${health.runs}` : plural(health.runs, "run")}</span>
    </span>
  );
}

const STATUS_LABEL: Record<SkillStatus, string> = { draft: "Draft", published: "Published", archived: "Archived" };
export function SkillStatusPill({ status }: { status: SkillStatus }) {
  return <span className={`pill pill--${status}`}>{STATUS_LABEL[status]}</span>;
}

const REC_LABEL: Record<RecordingStatus, string> = {
  received: "Uploaded",
  processing: "Writing draft",
  ready: "Draft ready",
  failed: "Failed",
};
export function RecordingStatusPill({ status }: { status: RecordingStatus }) {
  return (
    <span className={`pill pill--rec-${status}`}>
      {(status === "processing" || status === "received") && <Loader2 size={12} className="spin" aria-hidden />}
      {REC_LABEL[status]}
    </span>
  );
}

export const VISIBILITY_LABEL: Record<Visibility, string> = { private: "Only me", team: "Team", org: "Everyone" };

export function Empty({ art, title, children, actions }: { art?: ReactNode; title: string; children?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="empty">
      {art}
      <h2>{title}</h2>
      {children && <div className="empty__body">{children}</div>}
      {actions && <div className="empty__actions">{actions}</div>}
    </div>
  );
}

export function ErrorNote({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  return (
    <div className="note note--bad" role="alert">
      <CircleX size={18} aria-hidden />
      <div>
        <p>{error.message}</p>
        {onRetry && (
          <button className="link" onClick={onRetry}>
            Try again
          </button>
        )}
      </div>
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div className="loading" role="status">
      <Loader2 size={18} className="spin" aria-hidden />
      <span>{label}</span>
    </div>
  );
}

/** Renders text with {{input}} references shown as chips. */
export function WithInputs({ text }: { text: string }) {
  const parts = text.split(/(\{\{\s*[a-zA-Z0-9_]+\s*\}\})/g);
  return (
    <>
      {parts.map((p, i) => {
        const m = /^\{\{\s*([a-zA-Z0-9_]+)\s*\}\}$/.exec(p);
        return m ? (
          <span className="var" key={i}>
            {m[1]}
          </span>
        ) : (
          <span key={i}>{p}</span>
        );
      })}
    </>
  );
}

export function RecordWithDotSteps() {
  return (
    <ol className="howto">
      <li>
        <span>
        <strong>Click the dot</strong> floating on your Mac screen. Its eyes open while it watches.
        </span>
      </li>
      <li>
        <span>
        <strong>Do the task the way you normally would.</strong> Passwords and personal details are never captured.
        </span>
      </li>
      <li>
        <span>
        <strong>Click the dot again to stop.</strong> A draft shows up in My recordings within a minute for you to review and publish.
        </span>
      </li>
    </ol>
  );
}
