import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Navigate, Outlet, useLocation } from "react-router-dom";
import { LogOut, Monitor, Moon, Plus, Smile, Sun } from "lucide-react";
import { useAuth } from "../auth/AuthContext";
import { USE_MOCK } from "../api";
import { getTheme, setTheme, type ThemeChoice } from "../lib/theme";
import { quietMotion, useScrollChrome, useScrollReveal } from "../lib/motion";
import { Avatar } from "./Avatar";
import { AvatarPicker } from "./AvatarPicker";
import { Dot } from "./Dot";
import { Spinner } from "./bits";

function ThemeToggle() {
  const [t, setT] = useState<ThemeChoice>(getTheme());
  const next: Record<ThemeChoice, ThemeChoice> = { system: "light", light: "dark", dark: "system" };
  const Icon = t === "light" ? Sun : t === "dark" ? Moon : Monitor;
  const label = t === "system" ? "Theme: match system" : `Theme: ${t}`;
  return (
    <button
      className="icon-btn"
      onClick={() => {
        setTheme(next[t]);
        setT(next[t]);
      }}
      title={label}
      aria-label={label}
    >
      <Icon size={18} />
    </button>
  );
}

function UserMenu() {
  const { user, signOut } = useAuth();
  const [open, setOpen] = useState(false);
  const [picking, setPicking] = useState<null | "first" | "change">(null);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setOpen(false);
    const k = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", h);
    document.addEventListener("keydown", k);
    return () => {
      document.removeEventListener("mousedown", h);
      document.removeEventListener("keydown", k);
    };
  }, [open]);

  // Offer the picker once, the first time someone signs in.
  useEffect(() => {
    if (!user || quietMotion()) return;
    let picked = "1";
    try {
      picked = localStorage.getItem("ws:avatar-picked") ?? "";
    } catch {
      /* no storage: don't nag */
    }
    if (!picked && (!user.avatar || user.avatar === "orb")) {
      const t = setTimeout(() => setPicking("first"), 900);
      return () => clearTimeout(t);
    }
  }, [user?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  function closePicker() {
    try {
      localStorage.setItem("ws:avatar-picked", "1");
    } catch {
      /* ignore */
    }
    setPicking(null);
  }

  if (!user) return null;
  return (
    <div className="usermenu" ref={ref}>
      <button className="avatar" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-label={`Account: ${user.name}`}>
        <Avatar kind={user.avatar} size={36} />
      </button>
      {picking && <AvatarPicker firstTime={picking === "first"} onClose={closePicker} />}
      {open && (
        <div className="usermenu__panel" role="menu">
          <div className="usermenu__me av-host">
            <Avatar kind={user.avatar} size={44} awake />
            <div>
              <p className="usermenu__name">{user.name}</p>
              <p className="usermenu__email">{user.email}</p>
            </div>
          </div>
          {user.teams.length > 0 && <p className="usermenu__teams">{user.teams.map((t) => t.name).join(", ")}</p>}
          {user.role === "admin" && <p className="usermenu__teams">Administrator</p>}
          <button
            className="usermenu__item"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              setPicking("change");
            }}
          >
            <Smile size={16} aria-hidden /> Change your dot
          </button>
          <button className="usermenu__item" role="menuitem" onClick={signOut}>
            <LogOut size={16} aria-hidden /> Sign out
          </button>
        </div>
      )}
    </div>
  );
}

export function AppShell() {
  const { user } = useAuth();
  const main = useRef<HTMLElement>(null);
  useScrollChrome();
  useScrollReveal(main);
  return (
    <div className="shell">
      <div className="progress" aria-hidden />
      <a href="#main" className="skip">
        Skip to content
      </a>
      <header className="topbar">
        <div className="topbar__inner">
          <Link to="/" className="brand" aria-label="Work Shadower home">
            <Dot size={30} />
            <span>Work Shadower</span>
          </Link>
          <nav className="nav" aria-label="Main">
            <NavLink to="/" end>
              Library
            </NavLink>
            <NavLink to="/recordings">My recordings</NavLink>
            {user?.role === "admin" && <NavLink to="/admin">Admin</NavLink>}
          </nav>
          <div className="topbar__right">
            {USE_MOCK && (
              <span className="mockflag" title="Running against the in-memory mock API (VITE_USE_MOCK=1)">
                Mock data
              </span>
            )}
            <Link to="/skills/new" className="btn btn--quiet btn--sm newskill">
              <Plus size={16} aria-hidden />
              <span>New skill</span>
            </Link>
            <ThemeToggle />
            <UserMenu />
          </div>
        </div>
      </header>
      <main id="main" className="main" ref={main}>
        <Outlet />
      </main>
    </div>
  );
}

export function RequireAuth() {
  const { user, loading } = useAuth();
  const loc = useLocation();
  if (loading)
    return (
      <div className="splash">
        <Spinner label="Signing you in" />
      </div>
    );
  if (!user) return <Navigate to={`/login?next=${encodeURIComponent(loc.pathname + loc.search)}`} replace />;
  return <Outlet />;
}

export function RequireAdmin() {
  const { user } = useAuth();
  if (user?.role !== "admin") return <Navigate to="/" replace />;
  return <Outlet />;
}
