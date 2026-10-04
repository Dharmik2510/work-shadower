import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { PenLine, Search, X } from "lucide-react";
import { api, type SkillStatus, type SkillSummary, type TeamRef } from "../api";
import { useUser } from "../auth/AuthContext";
import { Avatar } from "../components/Avatar";
import { Empty, ErrorNote, HealthBadge, RecordWithDotSteps, SkillStatusPill, Spinner, VISIBILITY_LABEL } from "../components/bits";
import { relTime } from "../lib/format";
import { useAsync, useDebounced } from "../lib/useAsync";

const SUGGESTIONS = ["file an auto claim", "add a driver to a policy", "reset a broker password"];

export default function Library() {
  const user = useUser();
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const team = params.get("team") ?? "";
  const mine = params.get("mine") === "1";
  const status = (params.get("status") ?? "") as SkillStatus | "";
  const [focused, setFocused] = useState(false);
  // A short hello when the library opens: the avatar wakes up and smiles, then settles.
  const [greeting, setGreeting] = useState(true);
  useEffect(() => {
    const t = setTimeout(() => setGreeting(false), 1800);
    return () => clearTimeout(t);
  }, []);
  const input = useRef<HTMLInputElement>(null);
  const dq = useDebounced(q.trim(), 250);

  const set = (k: string, v: string) => {
    const p = new URLSearchParams(params);
    if (v) p.set(k, v);
    else p.delete(k);
    setParams(p, { replace: true });
  };

  const teams = useAsync(() => api.teams(), []);
  const searching = dq.length > 0;
  const results = useAsync<SkillSummary[]>(async () => {
    if (searching) {
      // /search covers published, visible skills only; team & mine are applied here.
      const r = await api.search(dq, 50);
      return r.items.filter((s) => (!team || s.team?.id === team) && (!mine || s.owner.id === user.id));
    }
    const r = await api.listSkills({ team_id: team || undefined, mine: mine || undefined, status: status || undefined, limit: 50 });
    return r.items;
  }, [dq, team, mine, status]);

  const items = results.data ?? [];
  const look = Math.min(1, q.length / 18) * 2 - 1;
  const filtered = !!(team || mine || status);
  const teamName = useMemo(() => teams.data?.items.find((t: TeamRef) => t.id === team)?.name, [teams.data, team]);

  return (
    <div className="page library">
      <section className="ask">
        <h1 className="ask__hello">Hi {user.name.split(" ")[0]}, what do you need to get done?</h1>
        <label htmlFor="ask" className="ask__label">
          Search the skill library
        </label>
        <div className={`ask__box ${focused || q ? "is-active" : ""}`}>
          <Avatar kind={user.avatar} size={56} awake={focused || !!q || greeting} happy={greeting || (focused && !q)} look={focused || q ? look : 0} className="ask__dot" />
          <input
            id="ask"
            ref={input}
            className="ask__input"
            type="search"
            autoComplete="off"
            placeholder="How do I…?"
            value={q}
            onChange={(e) => set("q", e.target.value)}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
          />
          {q ? (
            <button
              className="icon-btn ask__clear"
              aria-label="Clear search"
              onClick={() => {
                set("q", "");
                input.current?.focus();
              }}
            >
              <X size={20} />
            </button>
          ) : (
            <Search className="ask__icon" size={22} aria-hidden />
          )}
        </div>
        {!q && (
          <div className="ask__try">
            <span>Try</span>
            {SUGGESTIONS.map((s) => (
              <button key={s} className="chip chip--btn" onClick={() => set("q", s)}>
                {s}
              </button>
            ))}
          </div>
        )}
      </section>

      <div className="filters" role="group" aria-label="Filter skills">
        <div className="seg" role="radiogroup" aria-label="Whose skills">
          <button role="radio" aria-checked={!mine} className={!mine ? "is-on" : ""} onClick={() => set("mine", "")}>
            Everyone's
          </button>
          <button role="radio" aria-checked={mine} className={mine ? "is-on" : ""} onClick={() => set("mine", "1")}>
            Mine
          </button>
        </div>
        <label className="select">
          <span className="sr-only">Team</span>
          <select value={team} onChange={(e) => set("team", e.target.value)}>
            <option value="">All teams</option>
            {teams.data?.items.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
        <label className="select" title={searching ? "Search only covers published skills" : undefined}>
          <span className="sr-only">Status</span>
          <select value={searching ? "published" : status} disabled={searching} onChange={(e) => set("status", e.target.value)}>
            <option value="">Published and drafts</option>
            <option value="published">Published</option>
            <option value="draft">Drafts</option>
            <option value="archived">Archived</option>
          </select>
        </label>
        <p className="filters__count" aria-live="polite">
          {results.loading ? "Searching…" : results.data ? `${items.length} ${items.length === 1 ? "skill" : "skills"}` : ""}
        </p>
      </div>

      {results.error && <ErrorNote error={results.error} onRetry={results.reload} />}
      {!results.data && results.loading && <Spinner label="Loading skills" />}

      {results.data && items.length === 0 && !results.loading && (
        searching ? (
          <Empty
            art={<Avatar kind={user.avatar} size={64} awake idle />}
            title={`Nothing for “${dq}” yet`}
            actions={
              <>
                <Link to="/skills/new" className="btn btn--quiet">
                  <PenLine size={16} aria-hidden /> Write it by hand
                </Link>
                {filtered && (
                  <button className="btn btn--ghost" onClick={() => setParams(new URLSearchParams({ q }), { replace: true })}>
                    Clear filters
                  </button>
                )}
              </>
            }
          >
            <p>
              {filtered ? `No published skills match in ${teamName ?? "these filters"}. ` : "No published skill matches that. "}
              If you know how to do it, record it once with the dot and your colleagues can find it here.
            </p>
            <RecordWithDotSteps />
          </Empty>
        ) : (
          <Empty art={<Avatar kind={user.avatar} size={64} awake idle />} title={mine ? "You haven't recorded any skills yet" : "The library is empty"}>
            <p>Skills come from people doing their work once while the dot watches. Here's how to record your first one:</p>
            <RecordWithDotSteps />
          </Empty>
        )
      )}

      {items.length > 0 && (
        <ul className={`skills ${results.loading ? "is-stale" : ""}`}>
          {items.map((s) => (
            <SkillRow key={s.id} s={s} mine={s.owner.id === user.id} myAvatar={user.avatar} />
          ))}
        </ul>
      )}
    </div>
  );
}

function SkillRow({ s, mine, myAvatar }: { s: SkillSummary; mine: boolean; myAvatar?: string }) {
  const to = s.status === "draft" && mine ? `/skills/${s.id}/edit` : `/skills/${s.id}`;
  return (
    <li className="skill av-host">
      <div className="skill__main">
        <h3 className="skill__title">
          <Link to={to}>{s.title || "Untitled skill"}</Link>
        </h3>
        <p className="skill__goal">{s.goal}</p>
        <div className="skill__meta">
          {s.apps.slice(0, 3).map((a) => (
            <span key={a} className="chip">
              {a}
            </span>
          ))}
          {s.apps.length > 3 && <span className="chip chip--more">+{s.apps.length - 3}</span>}
          <span className="skill__by">
            <Avatar kind={mine ? myAvatar : s.owner.avatar} size={20} />
            {mine ? "You" : s.owner.name}
            {s.team && <span className="skill__team">{s.team.name}</span>}
          </span>
        </div>
      </div>
      <div className="skill__side">
        <HealthBadge health={s.health} />
        <div className="skill__ver">
          {s.status !== "published" && <SkillStatusPill status={s.status} />}
          {s.current_version > 0 && <span className="ver">v{s.current_version}</span>}
          {s.visibility !== "org" && <span className="vis">{VISIBILITY_LABEL[s.visibility]}</span>}
        </div>
        <span className="skill__when">Updated {relTime(s.updated_at)}</span>
      </div>
    </li>
  );
}
