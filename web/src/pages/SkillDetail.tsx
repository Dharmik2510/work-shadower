import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft, History, Link2, Lock, MonitorPlay, PenLine, ShieldAlert } from "lucide-react";
import { api, type SkillContent, type SkillStep } from "../api";
import { useUser } from "../auth/AuthContext";
import { AuthImage, Lightbox } from "../components/AuthImage";
import { Dot } from "../components/Dot";
import { ErrorNote, HealthBadge, SkillStatusPill, Spinner, VISIBILITY_LABEL, WithInputs } from "../components/bits";
import { useToast } from "../components/Toast";
import { dateTime, relTime } from "../lib/format";
import { useAsync } from "../lib/useAsync";

const ACTION_HINT: Record<string, string> = {
  open_app: "Opens an app",
  open_url: "Opens a web page",
  click: "Click",
  type: "Type",
  key: "Keyboard shortcut",
  menu: "Menu",
  wait: "You do this one",
};

export function deepLink(skillId: string, version: number) {
  return `workshadower://run?skill_id=${encodeURIComponent(skillId)}&version=${version}`;
}

export default function SkillDetail() {
  const { id = "" } = useParams();
  const user = useUser();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const skill = useAsync(() => api.getSkill(id), [id]);
  const versions = useAsync(() => api.listVersions(id).catch(() => ({ items: [] })), [id]);
  const flags = useAsync(() => api.config().catch(() => null), []);
  const s = skill.data;
  const latest = s?.current_version ?? 0;
  const vParam = Number(params.get("v")) || 0;
  const v = vParam && vParam !== latest ? vParam : 0;
  const old = useAsync(() => (v ? api.getVersion(id, v) : Promise.resolve(null)), [id, v]);
  const [launch, setLaunch] = useState<null | "trying" | "opened" | "fallback">(null);
  const [zoom, setZoom] = useState<{ url: string; alt: string } | null>(null);

  if (skill.error) return <div className="page"><BackLink /><ErrorNote error={skill.error} onRetry={skill.reload} /></div>;
  if (!s) return <div className="page"><Spinner label="Loading skill" /></div>;

  const isOwner = s.owner.id === user.id || user.role === "admin";
  const draftOnly = !s.published;
  const content: SkillContent | null = v ? old.data?.content ?? null : s.published ?? s.draft;
  const shownVersion = v || latest;
  const replayOff = flags.data && !flags.data.replay_enabled;

  function doItForMe() {
    setLaunch("trying");
    let left = false;
    const onBlur = () => (left = true);
    window.addEventListener("blur", onBlur, { once: true });
    document.addEventListener("visibilitychange", onBlur, { once: true });
    window.location.href = deepLink(s!.id, shownVersion);
    setTimeout(() => {
      window.removeEventListener("blur", onBlur);
      document.removeEventListener("visibilitychange", onBlur);
      setLaunch(left ? "opened" : "fallback");
    }, 1600);
  }

  async function copyLink() {
    const url = `${window.location.origin}/skills/${s!.id}${v ? `?v=${v}` : ""}`;
    try {
      await navigator.clipboard.writeText(url);
      toast("Link copied", "good");
    } catch {
      window.prompt("Copy this link", url);
    }
  }

  const irreversible = content?.steps.filter((st) => st.irreversible).length ?? 0;

  return (
    <div className="page detail">
      <BackLink />
      {draftOnly && (
        <div className="note note--draft">
          <Lock size={18} aria-hidden />
          <div>
            <p>
              <strong>This is an unpublished draft.</strong> {isOwner ? "Only you can see it until you publish it." : "Only its owner can publish it."}
            </p>
            {isOwner && (
              <Link className="link" to={`/skills/${s.id}/edit`}>
                Review and publish
              </Link>
            )}
          </div>
        </div>
      )}

      <div className="detail__grid">
        <header className="detail__head">
          <h1>{content?.title ?? "Loading…"}</h1>
          {content && <p className="detail__goal">{content.goal}</p>}
          <dl className="facts">
            <div>
              <dt>Owner</dt>
              <dd>{s.owner.id === user.id ? "You" : s.owner.name}</dd>
            </div>
            {s.team && (
              <div>
                <dt>Team</dt>
                <dd>{s.team.name}</dd>
              </div>
            )}
            <div>
              <dt>Shared with</dt>
              <dd>{VISIBILITY_LABEL[s.visibility]}</dd>
            </div>
            <div>
              <dt>Updated</dt>
              <dd title={dateTime(s.updated_at)}>{relTime(s.updated_at)}</dd>
            </div>
            {content && content.apps.length > 0 && (
              <div>
                <dt>Apps</dt>
                <dd>{content.apps.join(", ")}</dd>
              </div>
            )}
          </dl>
        </header>

        <aside className="runpanel" aria-label="Run this skill">
          <button className="btn btn--primary btn--lg runpanel__go" onClick={doItForMe} disabled={!!replayOff || draftOnly}>
            <MonitorPlay size={18} aria-hidden />
            Do it for me on my Mac
          </button>
          {replayOff && <p className="hint">An admin has paused “Do it for me” for now. You can still follow the steps yourself.</p>}
          {draftOnly && !replayOff && <p className="hint">Publish the skill to run it on a Mac.</p>}
          {!replayOff && !draftOnly && (
            <p className="hint">
              The dot runs each step and stops to ask before anything that can't be undone{irreversible ? ` (${irreversible} here)` : ""}.
            </p>
          )}
          {launch && (
            <div className={`launch launch--${launch}`} role="status">
              {launch === "trying" && (
                <p>
                  <Dot size={20} awake /> Opening Work Shadower on your Mac…
                </p>
              )}
              {launch === "opened" && <p>Switched to your Mac app. Look for the dot. It'll ask for any inputs first.</p>}
              {launch === "fallback" && (
                <>
                  <p>
                    <strong>Nothing opened?</strong> The Work Shadower Mac app needs to be installed and running for “Do it for me”.
                  </p>
                  <p>Ask your IT service desk for “Work Shadower” in Self Service, or follow the steps below yourself.</p>
                  <button className="link" onClick={() => setLaunch(null)}>
                    Dismiss
                  </button>
                </>
              )}
            </div>
          )}

          <div className="runpanel__row">
            <button className="btn btn--quiet btn--sm" onClick={copyLink}>
              <Link2 size={16} aria-hidden /> Copy link
            </button>
            {isOwner && (
              <Link className="btn btn--quiet btn--sm" to={`/skills/${s.id}/edit`}>
                <PenLine size={16} aria-hidden /> {s.draft ? "Edit draft" : "Edit"}
              </Link>
            )}
          </div>

          <div className="runpanel__health">
            <div className="runpanel__label">Health</div>
            <HealthBadge health={s.health} />
            {s.health.last_run_at && <p className="hint">Last run {relTime(s.health.last_run_at)}</p>}
            <Link className="link" to={`/skills/${s.id}/runs`}>
              <History size={14} aria-hidden /> Run history
            </Link>
          </div>

          {latest > 0 && (
            <label className="runpanel__ver">
              <span className="runpanel__label">Version</span>
              <select
                value={shownVersion}
                onChange={(e) => {
                  const n = Number(e.target.value);
                  const p = new URLSearchParams(params);
                  if (n === latest) p.delete("v");
                  else p.set("v", String(n));
                  setParams(p, { replace: true });
                }}
              >
                {(versions.data?.items.length ? versions.data.items : [{ version: latest, created_at: s.updated_at, created_by: "" }]).map((x) => (
                  <option key={x.version} value={x.version}>
                    v{x.version}
                    {x.version === latest ? " (latest)" : ""}, {relTime(x.created_at)}
                  </option>
                ))}
              </select>
            </label>
          )}
          {s.status !== "published" && (
            <p>
              <SkillStatusPill status={s.status} />
            </p>
          )}
        </aside>

        <div className="detail__body">
          {v > 0 && (
            <div className="note">
              <History size={18} aria-hidden />
              <p>
                You're reading v{v}, an older version.{" "}
                <button className="link" onClick={() => setParams({}, { replace: true })}>
                  Show latest (v{latest})
                </button>
              </p>
            </div>
          )}
          {old.error && <ErrorNote error={old.error} />}
          {!content && <Spinner label="Loading version" />}
          {content && (
            <>
              {(content.prerequisites.length > 0 || content.inputs.length > 0) && (
                <section className="prep">
                  {content.prerequisites.length > 0 && (
                    <div>
                      <h2>Before you start</h2>
                      <ul className="checks">
                        {content.prerequisites.map((p, i) => (
                          <li key={i}>{p}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {content.inputs.length > 0 && (
                    <div>
                      <h2>Have these ready</h2>
                      <dl className="inputs">
                        {content.inputs.map((inp) => (
                          <div key={inp.name}>
                            <dt>
                              <span className="var">{inp.name}</span>
                            </dt>
                            <dd>
                              {inp.description}
                              {inp.example && <span className="inputs__eg">e.g. {inp.example}</span>}
                            </dd>
                          </div>
                        ))}
                      </dl>
                    </div>
                  )}
                </section>
              )}

              <section>
                <h2 className="steps__h">
                  Steps <span className="count">{content.steps.length}</span>
                </h2>
                <ol className="steps">
                  {content.steps.map((st, i) => (
                    <StepView key={i} st={st} n={i + 1} onZoom={(url) => setZoom({ url, alt: `Step ${i + 1}: ${st.title}` })} />
                  ))}
                </ol>
                {content.tags.length > 0 && (
                  <div className="tags">
                    {content.tags.map((t) => (
                      <Link key={t} className="chip chip--btn" to={`/?q=${encodeURIComponent(t)}`}>
                        {t}
                      </Link>
                    ))}
                  </div>
                )}
              </section>
            </>
          )}
        </div>
      </div>
      {zoom && <Lightbox url={zoom.url} alt={zoom.alt} onClose={() => setZoom(null)} />}
    </div>
  );
}

function StepView({ st, n, onZoom }: { st: SkillStep; n: number; onZoom(url: string): void }) {
  return (
    <li className={`step ${st.irreversible ? "step--irr" : ""} ${st.screenshot_sha256 ? "step--shot" : ""}`}>
      <span className="step__n" aria-hidden>
        {n}
      </span>
      <div className="step__text">
        <h3>{st.title}</h3>
        <p>
          <WithInputs text={st.instruction} />
        </p>
        <p className="step__app">
          {st.app}
          {st.action?.type && <span className="step__act">{ACTION_HINT[st.action.type] ?? st.action.type}</span>}
          {st.action?.key && <kbd>{st.action.key}</kbd>}
        </p>
        {st.irreversible && (
          <p className="irr">
            <ShieldAlert size={16} aria-hidden />
            <span>
              <strong>Can't be undone.</strong> Double-check before you do this. The Mac app always stops here and waits for your click.
            </span>
          </p>
        )}
      </div>
      {st.screenshot_sha256 && <AuthImage sha256={st.screenshot_sha256} alt={`Screenshot for step ${n}: ${st.title}`} className="step__img" onOpen={onZoom} />}
    </li>
  );
}

function BackLink() {
  return (
    <Link to="/" className="back">
      <ArrowLeft size={16} aria-hidden /> Library
    </Link>
  );
}

export function useTitle(t: string) {
  useEffect(() => {
    document.title = t ? `${t} · Work Shadower` : "Work Shadower";
  }, [t]);
}
