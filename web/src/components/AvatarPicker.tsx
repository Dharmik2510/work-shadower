import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { api, type AvatarKind } from "../api";
import { useAuth } from "../auth/AuthContext";
import { Avatar, AVATARS, isAvatarKind } from "./Avatar";
import { useToast } from "./Toast";

/**
 * "Pick your dot": a big live preview of the chosen character, and the five choices underneath.
 * Hovering a choice makes it smile; choosing one swaps the preview with a little pop.
 * Saved to the server, so the same character shows on your Mac and next to your skills.
 */
export function AvatarPicker({ onClose, firstTime = false }: { onClose(): void; firstTime?: boolean }) {
  const { user, setUser } = useAuth();
  const toast = useToast();
  const current: AvatarKind = isAvatarKind(user?.avatar) ? user!.avatar! : "orb";
  const [pick, setPick] = useState<AvatarKind>(current);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [popKey, setPopKey] = useState(0);
  const card = useRef<HTMLDivElement>(null);
  const info = AVATARS.find((a) => a.kind === pick)!;

  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    card.current?.querySelector<HTMLElement>(`[data-kind="${current}"]`)?.focus();
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", k);
    return () => {
      document.removeEventListener("keydown", k);
      prev?.focus?.();
    };
  }, [current, onClose]);

  function choose(k: AvatarKind) {
    if (k === pick) return;
    setPick(k);
    setPopKey((n) => n + 1);
  }

  function onKey(e: React.KeyboardEvent, i: number) {
    const d = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
    if (!d) return;
    e.preventDefault();
    const next = AVATARS[(i + d + AVATARS.length) % AVATARS.length]!.kind;
    choose(next);
    card.current?.querySelector<HTMLElement>(`[data-kind="${next}"]`)?.focus();
  }

  async function save() {
    if (pick === current && !firstTime) return onClose();
    setSaving(true);
    setError(null);
    try {
      const u = await api.updateMe({ avatar: pick });
      setUser(u);
      try {
        localStorage.setItem("ws:avatar-picked", "1");
      } catch {
        /* storage unavailable: the picker may show once more, which is harmless */
      }
      toast(`${info.name} is your dot now`, "good");
      onClose();
    } catch (e) {
      setError((e as Error).message);
      setSaving(false);
    }
  }

  // Portalled to <body>: the top bar's backdrop blur would otherwise trap a fixed overlay inside it.
  return createPortal(
    <div className="modal" role="dialog" aria-modal="true" aria-labelledby="pick-h" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal__card picker" ref={card}>
        <button className="icon-btn picker__x" aria-label="Close" onClick={onClose}>
          <X size={18} />
        </button>
        <div className="picker__stage" aria-hidden>
          <span className="picker__halo" data-kind={pick} />
          <Avatar key={popKey} kind={pick} size={136} awake happy={popKey > 0} idle className="picker__hero" />
        </div>
        <h2 id="pick-h" className="picker__title">
          {firstTime ? "Pick your dot" : "Change your dot"}
        </h2>
        <p className="picker__lede">It sits on your Mac while you work and shows next to the skills you share.</p>
        <div className="picker__row" role="radiogroup" aria-labelledby="pick-h">
          {AVATARS.map((a, i) => (
            <button
              key={a.kind}
              data-kind={a.kind}
              role="radio"
              aria-checked={pick === a.kind}
              tabIndex={pick === a.kind ? 0 : -1}
              className={`picker__opt ${pick === a.kind ? "is-on" : ""}`}
              onClick={() => choose(a.kind)}
              onKeyDown={(e) => onKey(e, i)}
            >
              <Avatar kind={a.kind} size={52} awake={pick === a.kind} />
              <span className="picker__name">{a.name}</span>
            </button>
          ))}
        </div>
        <p className="picker__blurb" aria-live="polite">
          <strong>{info.name}.</strong> {info.blurb}
        </p>
        {error && <p className="picker__err" role="alert">{error}</p>}
        <div className="modal__actions">
          <button className="btn btn--ghost" onClick={onClose}>
            {firstTime ? "Later" : "Cancel"}
          </button>
          <button className="btn btn--primary" onClick={save} disabled={saving}>
            {saving ? "Saving…" : `Use ${info.name}`}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
