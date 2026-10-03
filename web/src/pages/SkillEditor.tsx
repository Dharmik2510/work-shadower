import { useEffect, useMemo, useRef, useState, type DragEvent, type ReactNode } from "react";
import { Link, useBlocker, useNavigate, useParams } from "react-router-dom";
import {
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  Eye,
  EyeOff,
  GripVertical,
  Plus,
  ShieldAlert,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { api, ApiError, type Skill, type SkillContent, type SkillStep, type TeamRef, type Visibility } from "../api";
import { useUser } from "../auth/AuthContext";
import { AuthImage } from "../components/AuthImage";
import { ErrorNote, SkillStatusPill, Spinner } from "../components/bits";
import { useToast } from "../components/Toast";
import { relTime } from "../lib/format";
import { useAsync } from "../lib/useAsync";

const EMPTY: SkillContent = { title: "", goal: "", apps: [], prerequisites: [], inputs: [], steps: [], tags: [] };

const blankStep = (index: number): SkillStep => ({
  index,
  title: "",
  instruction: "",
  app: "",
  action: { type: "wait" },
  expect: null,
  screenshot_sha256: null,
  irreversible: false,
});

/** Editor-only step: keeps a hidden screenshot so "hide" can be undone before saving. */
type EStep = SkillStep & { _key: number; _hiddenShot?: string | null };
interface Draft {
  content: Omit<SkillContent, "steps">;
  steps: EStep[];
  visibility: Visibility;
  team_id: string | null;
}

let keySeq = 0;
function toDraft(c: SkillContent, visibility: Visibility, team_id: string | null): Draft {
  const { steps, ...rest } = c;
  return { content: { ...rest }, steps: steps.map((s) => ({ ...s, _key: ++keySeq })), visibility, team_id };
}
function fromDraft(d: Draft): SkillContent {
  return {
    ...d.content,
    title: d.content.title.trim(),
    goal: d.content.goal.trim(),
    prerequisites: d.content.prerequisites.map((p) => p.trim()).filter(Boolean),
    inputs: d.content.inputs.filter((i) => i.name.trim()),
    steps: d.steps.map(({ _key, _hiddenShot, ...s }, i) => ({ ...s, index: i + 1 })),
  };
}

const INPUT_NAME = /^[a-z][a-z0-9_]*$/;

function validate(d: Draft, forPublish: boolean): string[] {
  const errs: string[] = [];
  if (!d.content.title.trim()) errs.push("Give the skill a title.");
  if (forPublish && !d.content.goal.trim()) errs.push("Add a one-sentence goal so people know what this does.");
  if (forPublish && d.steps.length === 0) errs.push("Add at least one step.");
  if (forPublish && d.steps.some((s) => !s.title.trim())) errs.push("Every step needs a title.");
  const names = d.content.inputs.map((i) => i.name.trim()).filter(Boolean);
  const bad = names.filter((n) => !INPUT_NAME.test(n));
  if (bad.length) errs.push(`Input names use lowercase letters, numbers and underscores: ${bad.join(", ")}.`);
  if (new Set(names).size !== names.length) errs.push("Two inputs have the same name.");
  if (d.visibility === "team" && !d.team_id) errs.push("Pick the team to share with.");
  const known = new Set(names);
  const unknown = new Set<string>();
  for (const s of d.steps) for (const m of `${s.instruction} ${s.action?.text ?? ""}`.matchAll(/\{\{\s*([a-zA-Z0-9_]+)\s*\}\}/g)) if (!known.has(m[1]!)) unknown.add(m[1]!);
  if (unknown.size) errs.push(`Steps refer to inputs that don't exist: ${[...unknown].join(", ")}.`);
  return errs;
}

export default function SkillEditor() {
  const { id } = useParams();
  const isNew = !id;
  const user = useUser();
  const teams = useAsync(() => api.teams(), []);
  const skill = useAsync<Skill | null>(() => (id ? api.getSkill(id) : Promise.resolve(null)), [id]);

  if (skill.error) return <div className="page"><ErrorNote error={skill.error} onRetry={skill.reload} /></div>;
  if (!isNew && !skill.data) return <div className="page"><Spinner label="Loading draft" /></div>;
  const s = skill.data ?? null;
  if (s && s.owner.id !== user.id && user.role !== "admin")
    return (
      <div className="page">
        <ErrorNote error={new Error("Only the owner of this skill can edit it.")} />
        <Link className="link" to={`/skills/${s.id}`}>
          Back to the skill
        </Link>
      </div>
    );
  const defaultTeam = user.teams[0]?.id ?? null;
  const initial = s
    ? toDraft(s.draft ?? s.published ?? EMPTY, s.visibility, s.team?.id ?? null)
    : toDraft(EMPTY, defaultTeam ? "team" : "private", defaultTeam);
  return <Editor key={s?.id ?? "new"} skill={s} initial={initial} teams={teams.data?.items ?? user.teams} onSaved={(x) => skill.setData(x)} />;
}

function Editor({ skill, initial, teams, onSaved }: { skill: Skill | null; initial: Draft; teams: TeamRef[]; onSaved(s: Skill): void }) {
  const nav = useNavigate();
  const toast = useToast();
  const [d, setD] = useState<Draft>(initial);
  const [saved, setSaved] = useState(() => JSON.stringify(fromDraft(initial)) + initial.visibility + initial.team_id);
  const [busy, setBusy] = useState<null | "save" | "publish">(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [serverError, setServerError] = useState<Error | null>(null);
  const [focusStep, setFocusStep] = useState<number | null>(null);
  const bypass = useRef(false);

  const snapshot = JSON.stringify(fromDraft(d)) + d.visibility + d.team_id;
  const dirty = snapshot !== saved;

  // Unsaved-changes guard: in-app navigation and tab close.
  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty && !bypass.current && currentLocation.pathname !== nextLocation.pathname);
  useEffect(() => {
    if (!dirty) return;
    const h = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [dirty]);

  const set = <K extends keyof Draft["content"]>(k: K, v: Draft["content"][K]) => setD((x) => ({ ...x, content: { ...x.content, [k]: v } }));
  const setStep = (i: number, patch: Partial<EStep>) => setD((x) => ({ ...x, steps: x.steps.map((s, j) => (j === i ? { ...s, ...patch } : s)) }));
  const moveStep = (from: number, to: number) =>
    setD((x) => {
      if (to < 0 || to >= x.steps.length || from === to) return x;
      const steps = [...x.steps];
      const [it] = steps.splice(from, 1);
      steps.splice(to, 0, it!);
      return { ...x, steps };
    });

  async function persist(): Promise<Skill> {
    const content = fromDraft(d);
    const body = { content, visibility: d.visibility, team_id: d.team_id };
    const result = skill ? await api.patchSkill(skill.id, body) : await api.createSkill(body);
    setSaved(snapshot);
    onSaved(result);
    return result;
  }

  async function save() {
    const errs = validate(d, false);
    setErrors(errs);
    if (errs.length) return;
    setBusy("save");
    setServerError(null);
    try {
      const r = await persist();
      toast("Draft saved", "good");
      if (!skill) {
        bypass.current = true;
        nav(`/skills/${r.id}/edit`, { replace: true });
      }
    } catch (e) {
      setServerError(e as Error);
    } finally {
      setBusy(null);
    }
  }

  async function publish() {
    const errs = validate(d, true);
    setErrors(errs);
    if (errs.length) return;
    setBusy("publish");
    setServerError(null);
    try {
      const r = dirty || !skill ? await persist() : skill;
      const p = await api.publishSkill(r.id);
      toast(`Published v${p.current_version}`, "good");
      bypass.current = true;
      nav(`/skills/${p.id}`);
    } catch (e) {
      setServerError(e instanceof ApiError ? e : new Error((e as Error).message));
      setBusy(null);
    }
  }

  const inputNames = d.content.inputs.map((i) => i.name.trim()).filter((n) => INPUT_NAME.test(n));
  const title = skill ? (skill.published && !skill.draft ? "Edit skill" : "Review draft") : "New skill";

  return (
    <div className="page editor">
      <div className="editor__bar">
        <div className="editor__bar-inner">
          <Link to={skill ? `/skills/${skill.id}` : "/"} className="back">
            <ArrowLeft size={16} aria-hidden /> {skill ? "Skill" : "Library"}
          </Link>
          <div className="editor__state">
            <span className="editor__title">{title}</span>
            {skill && <SkillStatusPill status={skill.status} />}
            <span className={`dirty ${dirty ? "is-dirty" : ""}`} aria-live="polite">
              {dirty ? "Unsaved changes" : skill ? `Saved ${relTime(skill.updated_at)}` : "Not saved yet"}
            </span>
          </div>
          <div className="editor__actions">
            <button className="btn btn--quiet" onClick={save} disabled={!!busy || (!dirty && !!skill)}>
              {busy === "save" ? "Saving…" : "Save draft"}
            </button>
            <button className="btn btn--primary" onClick={publish} disabled={!!busy}>
              <Upload size={16} aria-hidden />
              {busy === "publish" ? "Publishing…" : skill?.current_version ? `Publish v${skill.current_version + 1}` : "Publish"}
            </button>
          </div>
        </div>
      </div>

      {skill?.source_recording_id && !skill.published && (
        <p className="editor__intro">
          The dot wrote this draft from your recording. Check each step reads clearly for someone who has never done it, swap typed values for inputs, and hide any screenshot that shows customer details.
        </p>
      )}

      {(errors.length > 0 || serverError) && (
        <div className="note note--bad" role="alert">
          <X size={18} aria-hidden />
          <div>
            {serverError && <p>{serverError.message}</p>}
            {errors.length > 0 && (
              <ul>
                {errors.map((e) => (
                  <li key={e}>{e}</li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}

      <Section title="Basics">
        <label className="field">
          <span>Title</span>
          <input value={d.content.title} onChange={(e) => set("title", e.target.value)} placeholder="Start with a verb, e.g. File a new auto claim" maxLength={140} />
        </label>
        <label className="field">
          <span>Goal</span>
          <textarea rows={2} value={d.content.goal} onChange={(e) => set("goal", e.target.value)} placeholder="One sentence on what this gets done and for whom" />
        </label>
        <div className="field-row">
          <TagField label="Apps" value={d.content.apps} onChange={(v) => set("apps", v)} placeholder="Add an app and press Enter" />
          <TagField label="Tags" value={d.content.tags} onChange={(v) => set("tags", v)} placeholder="Words people might search for" />
        </div>
      </Section>

      <Section title="Who can see it">
        <div className="radios" role="radiogroup" aria-label="Visibility">
          {(
            [
              ["private", "Only me", "Nobody else can find or open it."],
              ["team", "My team", "Members of the chosen team."],
              ["org", "Everyone", "Anyone in the company can search for it."],
            ] as const
          ).map(([v, label, help]) => (
            <label key={v} className={`radio ${d.visibility === v ? "is-on" : ""}`}>
              <input type="radio" name="vis" checked={d.visibility === v} onChange={() => setD((x) => ({ ...x, visibility: v }))} />
              <span className="radio__label">{label}</span>
              <span className="radio__help">{help}</span>
            </label>
          ))}
        </div>
        <label className="field field--inline">
          <span>Team</span>
          <select value={d.team_id ?? ""} onChange={(e) => setD((x) => ({ ...x, team_id: e.target.value || null }))}>
            <option value="">No team</option>
            {teams.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
      </Section>

      <Section title="Before you start" hint="What someone needs in place first, like access or documents.">
        <ListField value={d.content.prerequisites} onChange={(v) => set("prerequisites", v)} placeholder="e.g. ClaimCenter access with the Claims Intake role" addLabel="Add prerequisite" />
      </Section>

      <Section title="Inputs" hint="Values that change every time. Steps refer to them as chips, and the Mac app asks for them before it starts.">
        {d.content.inputs.length > 0 && (
          <div className="inputs-ed">
            <div className="inputs-ed__head" aria-hidden>
              <span>Name</span>
              <span>Description</span>
              <span>Example</span>
              <span />
            </div>
            {d.content.inputs.map((inp, i) => (
              <div className="inputs-ed__row" key={i}>
                <input
                  aria-label="Input name"
                  className={inp.name && !INPUT_NAME.test(inp.name) ? "is-bad" : ""}
                  value={inp.name}
                  placeholder="policy_number"
                  onChange={(e) => set("inputs", d.content.inputs.map((x, j) => (j === i ? { ...x, name: e.target.value.replace(/\s+/g, "_").toLowerCase() } : x)))}
                />
                <input aria-label="Input description" value={inp.description} placeholder="What it is" onChange={(e) => set("inputs", d.content.inputs.map((x, j) => (j === i ? { ...x, description: e.target.value } : x)))} />
                <input aria-label="Example value" value={inp.example} placeholder="Example" onChange={(e) => set("inputs", d.content.inputs.map((x, j) => (j === i ? { ...x, example: e.target.value } : x)))} />
                <button className="icon-btn" aria-label={`Remove input ${inp.name}`} onClick={() => set("inputs", d.content.inputs.filter((_, j) => j !== i))}>
                  <Trash2 size={16} />
                </button>
              </div>
            ))}
          </div>
        )}
        <button className="btn btn--ghost btn--sm" onClick={() => set("inputs", [...d.content.inputs, { name: "", description: "", example: "" }])}>
          <Plus size={16} aria-hidden /> Add input
        </button>
      </Section>

      <Section title={`Steps`} count={d.steps.length} hint="Drag to reorder, or use the arrows.">
        <StepList d={d} inputNames={inputNames} setStep={setStep} moveStep={moveStep} focusStep={focusStep} remove={(i) => setD((x) => ({ ...x, steps: x.steps.filter((_, j) => j !== i) }))} />
        <button
          className="btn btn--ghost btn--sm"
          onClick={() => {
            setD((x) => ({ ...x, steps: [...x.steps, { ...blankStep(x.steps.length + 1), app: x.steps.at(-1)?.app ?? "", _key: ++keySeq }] }));
            setFocusStep(d.steps.length);
          }}
        >
          <Plus size={16} aria-hidden /> Add step
        </button>
      </Section>

      {blocker.state === "blocked" && (
        <div className="modal" role="dialog" aria-modal="true" aria-labelledby="leave-h">
          <div className="modal__card">
            <h2 id="leave-h">Leave without saving?</h2>
            <p>Your changes to this draft will be lost.</p>
            <div className="modal__actions">
              <button className="btn btn--quiet" onClick={() => blocker.reset()} autoFocus>
                Keep editing
              </button>
              <button className="btn btn--danger" onClick={() => blocker.proceed()}>
                Discard changes
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function StepList({
  d,
  inputNames,
  setStep,
  moveStep,
  remove,
  focusStep,
}: {
  d: Draft;
  inputNames: string[];
  setStep(i: number, p: Partial<EStep>): void;
  moveStep(a: number, b: number): void;
  remove(i: number): void;
  focusStep: number | null;
}) {
  const [drag, setDrag] = useState<number | null>(null);
  const [over, setOver] = useState<number | null>(null);
  const textRefs = useRef<Record<number, HTMLTextAreaElement | null>>({});
  const titleRefs = useRef<Record<number, HTMLInputElement | null>>({});
  const lastFocused = useRef<number | null>(null);

  useEffect(() => {
    if (focusStep !== null) titleRefs.current[d.steps[focusStep]?._key ?? -1]?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusStep, d.steps.length]);

  function insertVar(i: number, key: number, name: string) {
    const el = textRefs.current[key];
    const s = d.steps[i]!;
    const token = `{{${name}}}`;
    // Insert at the caret if the user has been typing in this box; otherwise append.
    const active = !!el && lastFocused.current === key;
    const tail = s.instruction.length;
    const start = active ? el.selectionStart ?? tail : tail;
    const end = active ? el.selectionEnd ?? start : tail;
    const pad = !active && tail > 0 && !/\s$/.test(s.instruction) ? " " : "";
    const next = s.instruction.slice(0, start) + pad + token + s.instruction.slice(end);
    const caret = start + pad.length + token.length;
    setStep(i, { instruction: next });
    requestAnimationFrame(() => {
      el?.focus();
      el?.setSelectionRange(caret, caret);
    });
  }

  if (d.steps.length === 0) return <p className="muted">No steps yet. Add the first thing someone should do.</p>;

  return (
    <ol className="ed-steps">
      {d.steps.map((s, i) => {
        const shot = s.screenshot_sha256 ?? s._hiddenShot ?? null;
        const hidden = !s.screenshot_sha256 && !!s._hiddenShot;
        return (
          <li
            key={s._key}
            className={`ed-step ${s.irreversible ? "ed-step--irr" : ""} ${drag === i ? "is-dragging" : ""} ${over === i && drag !== null && drag !== i ? (drag < i ? "is-over-after" : "is-over-before") : ""}`}
            onDragOver={(e: DragEvent) => {
              if (drag === null) return;
              e.preventDefault();
              setOver(i);
            }}
            onDrop={(e) => {
              e.preventDefault();
              if (drag !== null) moveStep(drag, i);
              setDrag(null);
              setOver(null);
            }}
          >
            <div className="ed-step__rail">
              <span
                className="ed-step__grip"
                draggable
                onDragStart={(e) => {
                  setDrag(i);
                  e.dataTransfer.effectAllowed = "move";
                  e.dataTransfer.setData("text/plain", String(i));
                }}
                onDragEnd={() => {
                  setDrag(null);
                  setOver(null);
                }}
                title="Drag to reorder"
                aria-hidden
              >
                <GripVertical size={16} />
              </span>
              <span className="step__n">{i + 1}</span>
              <button className="icon-btn icon-btn--sm" aria-label={`Move step ${i + 1} up`} disabled={i === 0} onClick={() => moveStep(i, i - 1)}>
                <ArrowUp size={15} />
              </button>
              <button className="icon-btn icon-btn--sm" aria-label={`Move step ${i + 1} down`} disabled={i === d.steps.length - 1} onClick={() => moveStep(i, i + 1)}>
                <ArrowDown size={15} />
              </button>
            </div>
            <div className="ed-step__body">
              <div className="ed-step__top">
                <input
                  ref={(el) => (titleRefs.current[s._key] = el)}
                  className="ed-step__title"
                  aria-label={`Step ${i + 1} title`}
                  value={s.title}
                  placeholder="Short step title"
                  onChange={(e) => setStep(i, { title: e.target.value })}
                />
                <button className="icon-btn" aria-label={`Delete step ${i + 1}`} onClick={() => remove(i)}>
                  <Trash2 size={16} />
                </button>
              </div>
              <textarea
                ref={(el) => (textRefs.current[s._key] = el)}
                aria-label={`Step ${i + 1} instruction`}
                rows={2}
                value={s.instruction}
                placeholder="What to do, written for someone doing it for the first time"
                onChange={(e) => setStep(i, { instruction: e.target.value })}
                onFocus={() => (lastFocused.current = s._key)}
              />
              {inputNames.length > 0 && (
                <div className="ed-step__vars">
                  <span>Insert</span>
                  {inputNames.map((n) => (
                    <button key={n} className="var var--btn" onClick={() => insertVar(i, s._key, n)} title={`Insert {{${n}}} at the cursor`}>
                      {n}
                    </button>
                  ))}
                </div>
              )}
              <div className="ed-step__opts">
                <label className="field field--inline field--sm">
                  <span>App</span>
                  <input value={s.app} onChange={(e) => setStep(i, { app: e.target.value })} placeholder="e.g. Google Chrome" />
                </label>
                <label className={`toggle ${s.irreversible ? "is-on toggle--warn" : ""}`}>
                  <input type="checkbox" checked={s.irreversible} onChange={(e) => setStep(i, { irreversible: e.target.checked })} />
                  <span className="toggle__track" aria-hidden />
                  <ShieldAlert size={15} aria-hidden />
                  Can't be undone
                </label>
              </div>
              <p className="ed-step__action">{describeAction(s)}</p>
              {shot && (
                <div className={`ed-step__shot ${hidden ? "is-hidden" : ""}`}>
                  {hidden ? (
                    <div className="shot shot--hidden">
                      <EyeOff size={18} aria-hidden />
                      <span>Screenshot hidden. It won't be shown to anyone.</span>
                    </div>
                  ) : (
                    <AuthImage sha256={shot} alt={`Screenshot for step ${i + 1}`} />
                  )}
                  <button
                    className="btn btn--ghost btn--sm"
                    onClick={() => (hidden ? setStep(i, { screenshot_sha256: s._hiddenShot!, _hiddenShot: null }) : setStep(i, { screenshot_sha256: null, _hiddenShot: shot }))}
                  >
                    {hidden ? <Eye size={15} aria-hidden /> : <EyeOff size={15} aria-hidden />}
                    {hidden ? "Show screenshot" : "Hide screenshot"}
                  </button>
                </div>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function describeAction(s: SkillStep): string {
  const a = s.action;
  const t = a?.target?.label ? `“${a.target.label}”` : "";
  switch (a?.type) {
    case "click":
      return `Recorded: click ${t || "an element"}${a.target?.window_title ? ` in ${a.target.window_title}` : ""}`;
    case "type":
      return `Recorded: type ${a.text ? `“${a.text}”` : "text"}${t ? ` into ${t}` : ""}`;
    case "key":
      return `Recorded: press ${a.key ?? "a shortcut"}`;
    case "menu":
      return `Recorded: choose ${t || "a menu item"}`;
    case "open_url":
      return `Recorded: open ${a.url ?? "a web page"}`;
    case "open_app":
      return `Recorded: open ${s.app || "an app"}`;
    default:
      return "Manual step: the Mac app will pause and ask the person to do this one.";
  }
}

function Section({ title, hint, count, children }: { title: string; hint?: string; count?: number; children: ReactNode }) {
  return (
    <section className="ed-sec">
      <div className="ed-sec__head">
        <h2>
          {title}
          {count !== undefined && <span className="count">{count}</span>}
        </h2>
        {hint && <p>{hint}</p>}
      </div>
      <div className="ed-sec__body">{children}</div>
    </section>
  );
}

function TagField({ label, value, onChange, placeholder }: { label: string; value: string[]; onChange(v: string[]): void; placeholder: string }) {
  const [text, setText] = useState("");
  const add = () => {
    const t = text.trim().replace(/,$/, "");
    if (t && !value.includes(t)) onChange([...value, t]);
    setText("");
  };
  const id = useMemo(() => `tag-${label.toLowerCase()}`, [label]);
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <div className="tagbox">
        {value.map((t) => (
          <span key={t} className="chip chip--rm">
            {t}
            <button aria-label={`Remove ${t}`} onClick={() => onChange(value.filter((x) => x !== t))}>
              <X size={12} />
            </button>
          </span>
        ))}
        <input
          id={id}
          value={text}
          placeholder={value.length ? "" : placeholder}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              add();
            } else if (e.key === "Backspace" && !text && value.length) onChange(value.slice(0, -1));
          }}
          onBlur={add}
        />
      </div>
    </div>
  );
}

function ListField({ value, onChange, placeholder, addLabel }: { value: string[]; onChange(v: string[]): void; placeholder: string; addLabel: string }) {
  return (
    <div className="listfield">
      {value.map((v, i) => (
        <div key={i} className="listfield__row">
          <input aria-label={`Prerequisite ${i + 1}`} value={v} placeholder={placeholder} onChange={(e) => onChange(value.map((x, j) => (j === i ? e.target.value : x)))} />
          <button className="icon-btn" aria-label={`Remove prerequisite ${i + 1}`} onClick={() => onChange(value.filter((_, j) => j !== i))}>
            <Trash2 size={16} />
          </button>
        </div>
      ))}
      <button className="btn btn--ghost btn--sm" onClick={() => onChange([...value, ""])}>
        <Plus size={16} aria-hidden /> {addLabel}
      </button>
    </div>
  );
}
