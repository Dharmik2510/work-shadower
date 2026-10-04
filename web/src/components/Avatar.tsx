import { useId, type CSSProperties } from "react";
import type { AvatarKind } from "../api";

/**
 * The five dot characters. Each is drawn on the same 64×64 face grid so they share one
 * set of expressions: sleepy at rest; on hover (or `awake`) the eyes open, a wide smile
 * appears, cheeks blush and the body gives a small hop. Every character has one signature
 * motion of its own (leaves wiggle, flame flickers, cloud puffs drift, antenna lights up).
 */
export interface AvatarInfo {
  kind: AvatarKind;
  name: string;
  blurb: string;
}

export const AVATARS: AvatarInfo[] = [
  { kind: "orb", name: "Orb", blurb: "The original. Calm, round, always watching your back." },
  { kind: "sprout", name: "Sprout", blurb: "Grows a little every time you teach it something." },
  { kind: "ember", name: "Ember", blurb: "Warm, quick and a bit impatient with slow forms." },
  { kind: "nimbus", name: "Nimbus", blurb: "Floats along quietly and remembers every step." },
  { kind: "pixel", name: "Pixel", blurb: "A tidy little robot that loves a repeatable task." },
];

export const AVATAR_KINDS = AVATARS.map((a) => a.kind);

export function isAvatarKind(v: unknown): v is AvatarKind {
  return typeof v === "string" && (AVATAR_KINDS as string[]).includes(v);
}

interface Face {
  /** eye centres */
  lx: number;
  rx: number;
  ey: number;
  /** mouth centre */
  my: number;
  eyeFill: string;
  pupil: string;
  lid: string; // closed-eye stroke
  mouth: string;
  cheek: string;
}

interface Spec {
  stops: [string, string, string];
  face: Face;
}

const SPECS: Record<AvatarKind, Spec> = {
  orb: {
    stops: ["#a8a2ff", "#5246e6", "#2a2091"],
    face: { lx: 23.5, rx: 40.5, ey: 35, my: 45.5, eyeFill: "#fff", pupil: "#1b1648", lid: "#fff", mouth: "#1b1648", cheek: "#ff8fb8" },
  },
  sprout: {
    stops: ["#a6f0c4", "#34b57a", "#13704a"],
    face: { lx: 24, rx: 40, ey: 37, my: 47.5, eyeFill: "#fff", pupil: "#0d3b28", lid: "#fff", mouth: "#0d3b28", cheek: "#ff9db0" },
  },
  ember: {
    stops: ["#ffe08a", "#ff8a3d", "#c9361f"],
    face: { lx: 24.5, rx: 39.5, ey: 41, my: 50.5, eyeFill: "#fff", pupil: "#4a1408", lid: "#fff", mouth: "#4a1408", cheek: "#ff5f6d" },
  },
  nimbus: {
    stops: ["#ffffff", "#d6e8ff", "#8fb5ee"],
    face: { lx: 25.5, rx: 40.5, ey: 41.5, my: 50.5, eyeFill: "#24345e", pupil: "#fff", lid: "#24345e", mouth: "#24345e", cheek: "#ffa9c4" },
  },
  pixel: {
    stops: ["#d6c2ff", "#8a5cf6", "#4e25b8"],
    face: { lx: 25, rx: 39, ey: 37, my: 46, eyeFill: "#8ff3ff", pupil: "#0b2a44", lid: "#8ff3ff", mouth: "#8ff3ff", cheek: "#ff8fd8" },
  },
};

function Body({ kind, fill }: { kind: AvatarKind; fill: string }) {
  switch (kind) {
    case "orb":
      return <circle cx="32" cy="35" r="26" fill={fill} />;
    case "sprout":
      return <path d="M32 14C48 14 57 22 57 38C57 52 47 60 32 60C17 60 7 52 7 38C7 22 16 14 32 14Z" fill={fill} />;
    case "ember":
      return (
        <path
          d="M33 6C37 15 54 23 54 41C54 53 44 61 32 61C20 61 10 53 10 41C10 31 16 25 21 18C22.5 23 25.5 26.5 29 27.5C27.5 20 29.5 12.5 33 6Z"
          fill={fill}
        />
      );
    case "nimbus":
      return (
        <path
          d="M17 60C8.5 60 3.5 53.5 4.5 46C5.5 38.5 11.5 34.5 17.5 35.5C17.5 25.5 25 18.5 34 18.5C42 18.5 48 23.5 50 30.5C57 30.5 61.5 37 60.5 45C59.5 53.5 53.5 60 46 60Z"
          fill={fill}
        />
      );
    case "pixel":
      return <rect x="9" y="16" width="46" height="44" rx="15" fill={fill} />;
  }
}

function Accessory({ kind, id }: { kind: AvatarKind; id: string }) {
  switch (kind) {
    case "orb":
      return <circle className="av__shine" cx="32" cy="35" r="26" fill={`url(#s${id})`} />;
    case "sprout":
      return (
        <g className="av__leaves">
          <path d="M32 15C32 11 32.5 8.5 33.5 6.5" stroke="#1f8a58" strokeWidth="2.4" strokeLinecap="round" fill="none" />
          <path className="av__leaf av__leaf--l" d="M33 9.5C28 3 19.5 2.5 15.5 7.5C20.5 12.5 28.5 13 33 9.5Z" fill="#5fd393" />
          <path className="av__leaf av__leaf--r" d="M33.5 7.5C37.5 1 46 -0.5 50 3.5C46.5 9 38.5 10.5 33.5 7.5Z" fill="#3dbb78" />
        </g>
      );
    case "ember":
      return <path className="av__flame" d="M33 14C35.5 20 44 25 44 34C44 39 39 41 34 38C37 33 34 27 33 23C31.5 27 28 29 28 34C24.5 30 26 21 33 14Z" fill="#ffe7a3" opacity="0.55" />;
    case "nimbus":
      return (
        <g className="av__puffs" fill="#ffffff">
          <circle cx="56" cy="20" r="3.2" opacity="0.9" />
          <circle cx="61" cy="14" r="1.9" opacity="0.7" />
        </g>
      );
    case "pixel":
      return (
        <g>
          <path d="M32 16V9" stroke="#3a1c8f" strokeWidth="2.6" strokeLinecap="round" />
          <circle className="av__bulb" cx="32" cy="7" r="4" fill="#ffd54d" />
          <rect x="5" y="32" width="5" height="11" rx="2.5" fill="#6d43d9" />
          <rect x="54" y="32" width="5" height="11" rx="2.5" fill="#6d43d9" />
        </g>
      );
  }
}

interface AvatarProps {
  kind?: AvatarKind | string | null;
  size?: number;
  /** Eyes open without hover (e.g. while a search box has focus). Hover always opens them. */
  awake?: boolean;
  /** Smile without hover (a greeting, a selected choice). Hover always smiles. */
  happy?: boolean;
  /** Pupils look -1 (left) … 1 (right). */
  look?: number;
  /** Gentle idle float. Off by default; use it for the one avatar that is "alive" on a page. */
  idle?: boolean;
  className?: string;
  label?: string;
  style?: CSSProperties;
}

export function Avatar({ kind, size = 32, awake = false, happy = false, look = 0, idle = false, className = "", label, style }: AvatarProps) {
  const k: AvatarKind = isAvatarKind(kind) ? kind : "orb";
  const { stops, face: f } = SPECS[k];
  const id = useId().replace(/:/g, "");
  const px = Math.max(-1, Math.min(1, look)) * 1.8;
  const front = k === "pixel" ? null : <ellipse cx="25" cy={k === "ember" ? 30 : 23} rx="9" ry="5" fill="#fff" opacity={k === "nimbus" ? 0.7 : 0.24} />;
  return (
    <span
      className={`av av--${k} ${awake ? "av--awake" : ""} ${happy ? "av--happy" : ""} ${idle ? "av--idle" : ""} ${className}`}
      style={{ width: size, height: size, ...style }}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    >
      <svg viewBox="0 0 64 64" width={size} height={size}>
        <defs>
          <radialGradient id={`g${id}`} cx="34%" cy="30%" r="80%">
            <stop offset="0" stopColor={stops[0]} />
            <stop offset="0.55" stopColor={stops[1]} />
            <stop offset="1" stopColor={stops[2]} />
          </radialGradient>
          <linearGradient id={`s${id}`} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0.35" stopColor="#fff" stopOpacity="0" />
            <stop offset="0.5" stopColor="#fff" stopOpacity="0.45" />
            <stop offset="0.65" stopColor="#fff" stopOpacity="0" />
          </linearGradient>
        </defs>
        <g className="av__body">
          {k === "pixel" && <Accessory kind={k} id={id} />}
          {k !== "pixel" && k !== "orb" && k !== "ember" && <Accessory kind={k} id={id} />}
          <Body kind={k} fill={`url(#g${id})`} />
          {k === "pixel" && <rect x="14" y="24" width="36" height="30" rx="11" fill="#170b3b" opacity="0.62" />}
          {(k === "orb" || k === "ember") && <Accessory kind={k} id={id} />}
          {front}

          {/* cheeks */}
          <g className="av__cheeks" fill={f.cheek}>
            <ellipse cx={f.lx - 5} cy={f.ey + 7} rx="4" ry="2.4" />
            <ellipse cx={f.rx + 5} cy={f.ey + 7} rx="4" ry="2.4" />
          </g>

          {/* sleepy eyes */}
          <g className="av__closed" stroke={f.lid} strokeWidth="3" strokeLinecap="round" fill="none">
            <path d={`M${f.lx - 5} ${f.ey} q5 4.2 10 0`} />
            <path d={`M${f.rx - 5} ${f.ey} q5 4.2 10 0`} />
          </g>
          {/* open eyes */}
          <g className="av__open">
            <g className="av__eyes">
              <ellipse cx={f.lx} cy={f.ey - 1} rx="5.4" ry="6.6" fill={f.eyeFill} />
              <ellipse cx={f.rx} cy={f.ey - 1} rx="5.4" ry="6.6" fill={f.eyeFill} />
              <g style={{ transform: `translateX(${px}px)` }} className="av__pupils">
                <circle cx={f.lx + 0.4} cy={f.ey} r="2.9" fill={f.pupil} />
                <circle cx={f.rx + 0.4} cy={f.ey} r="2.9" fill={f.pupil} />
                {k !== "nimbus" && k !== "pixel" && (
                  <>
                    <circle cx={f.lx + 1.5} cy={f.ey - 1.3} r="1" fill="#fff" />
                    <circle cx={f.rx + 1.5} cy={f.ey - 1.3} r="1" fill="#fff" />
                  </>
                )}
              </g>
            </g>
          </g>

          {/* calm mouth at rest */}
          <path className="av__mouth-rest" d={`M${32 - 3} ${f.my} q3 1.8 6 0`} stroke={f.mouth} strokeWidth="2" strokeLinecap="round" fill="none" />
          {/* the smile */}
          <g className="av__smile">
            <path d={`M${32 - 7.5} ${f.my - 1.5} Q32 ${f.my + 10} ${32 + 7.5} ${f.my - 1.5} Z`} fill={f.mouth} />
            {k !== "pixel" && <ellipse cx="32" cy={f.my + 4.2} rx="3.6" ry="2" fill="#ff7f9a" />}
          </g>
        </g>
      </svg>
    </span>
  );
}
