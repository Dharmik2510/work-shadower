import { useEffect } from "react";
import { useLocation } from "react-router-dom";

/** True when motion should be kept to a minimum (OS setting, or an automated browser taking screenshots). */
export function quietMotion(): boolean {
  if (typeof window === "undefined") return true;
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches || !!navigator.webdriver;
}

/**
 * Page chrome that reacts to scrolling:
 *  - `data-scrolled` on <html> once the page has moved (the top bar condenses and gains a shadow)
 *  - `--scroll` (0..1) on <html> for the reading-progress line (CSS scroll timelines are used
 *    where supported; this is the fallback)
 */
export function useScrollChrome(): void {
  useEffect(() => {
    const root = document.documentElement;
    let raf = 0;
    const update = () => {
      raf = 0;
      const y = window.scrollY;
      const max = Math.max(1, root.scrollHeight - window.innerHeight);
      root.toggleAttribute("data-scrolled", y > 8);
      root.style.setProperty("--scroll", String(Math.min(1, y / max)));
    };
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(update);
    };
    update();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      if (raf) cancelAnimationFrame(raf);
    };
  }, []);
}

/** Things that rise into place the first time they scroll into view. */
const REVEAL = [".skill", ".rec", ".steps > li", ".panel", ".ed-sec", ".tile", ".run-row", ".ask__try", ".filters", ".pagehead"].join(",");

/**
 * Scroll reveal for the whole app: list rows, panels and sections rise into place as they enter
 * the viewport, staggered when several arrive together. Watches the DOM so data that loads later
 * is covered too. Skipped entirely when motion should be quiet; content is never hidden without JS.
 */
export function useScrollReveal(container: React.RefObject<HTMLElement>): void {
  const { pathname } = useLocation();
  useEffect(() => {
    const host = container.current;
    if (!host || quietMotion() || !("IntersectionObserver" in window)) return;
    const root = document.documentElement;
    root.classList.add("reveal-on");
    const seen = new WeakSet<Element>();
    let batch = 0;
    let batchTimer = 0;

    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (!e.isIntersecting) continue;
          const el = e.target as HTMLElement;
          el.style.setProperty("--reveal-delay", `${Math.min(batch, 8) * 55}ms`);
          batch += 1;
          el.classList.add("is-in");
          io.unobserve(el);
        }
        clearTimeout(batchTimer);
        batchTimer = window.setTimeout(() => (batch = 0), 120);
      },
      { rootMargin: "0px 0px -6% 0px", threshold: 0.06 },
    );

    const scan = () => {
      host.querySelectorAll<HTMLElement>(REVEAL).forEach((el) => {
        if (seen.has(el)) return;
        seen.add(el);
        el.classList.add("reveal");
        io.observe(el);
      });
    };
    scan();
    const mo = new MutationObserver(() => scan());
    mo.observe(host, { childList: true, subtree: true });
    return () => {
      mo.disconnect();
      io.disconnect();
      clearTimeout(batchTimer);
    };
  }, [container, pathname]);
}
