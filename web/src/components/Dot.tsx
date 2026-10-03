import { useId } from "react";

interface DotProps {
  size?: number;
  /** Force eyes open (e.g. while the search box has focus). Hover always opens them. */
  awake?: boolean;
  /** Where the pupils look, -1 (left) … 1 (right). */
  look?: number;
  className?: string;
  label?: string;
}

/**
 * The Work Shadower brand mark: the floating indigo orb from the Mac app.
 * Sleepy closed eyes at rest; eyes open on hover or when `awake`.
 */
export function Dot({ size = 28, awake = false, look = 0, className = "", label }: DotProps) {
  const id = useId().replace(/:/g, "");
  const px = Math.max(-1, Math.min(1, look)) * 2.2;
  return (
    <span
      className={`dot ${awake ? "dot--awake" : ""} ${className}`}
      style={{ width: size, height: size }}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    >
      <svg viewBox="0 0 64 64" width={size} height={size}>
        <defs>
          <radialGradient id={`g${id}`} cx="34%" cy="28%" r="78%">
            <stop offset="0" stopColor="var(--orb-hi)" />
            <stop offset="0.55" stopColor="var(--orb-mid)" />
            <stop offset="1" stopColor="var(--orb-lo)" />
          </radialGradient>
        </defs>
        <circle cx="32" cy="32" r="30" fill={`url(#g${id})`} />
        <ellipse cx="24" cy="15" rx="9" ry="5" fill="#fff" opacity="0.22" />
        {/* closed, sleepy eyes */}
        <g className="dot__closed" stroke="#fff" strokeWidth="3.2" strokeLinecap="round" fill="none">
          <path d="M17 34 q5.5 4.5 11 0" />
          <path d="M36 34 q5.5 4.5 11 0" />
        </g>
        {/* open eyes */}
        <g className="dot__open">
          <ellipse cx="22.5" cy="32" rx="6" ry="7.4" fill="#fff" />
          <ellipse cx="41.5" cy="32" rx="6" ry="7.4" fill="#fff" />
          <g className="dot__pupils" style={{ transform: `translateX(${px}px)` }}>
            <circle cx="23" cy="33" r="3.3" fill="#1b1648" />
            <circle cx="42" cy="33" r="3.3" fill="#1b1648" />
            <circle cx="24.2" cy="31.6" r="1.1" fill="#fff" />
            <circle cx="43.2" cy="31.6" r="1.1" fill="#fff" />
          </g>
        </g>
      </svg>
    </span>
  );
}
