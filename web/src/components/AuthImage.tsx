import { useEffect, useState } from "react";
import { ImageOff } from "lucide-react";
import { api } from "../api";

// Screenshots require the bearer token, so we fetch them as blobs and hand
// <img> an object URL. URLs are cached per sha256 for the whole session
// (content-addressed, so they never go stale).
const cache = new Map<string, Promise<string>>();

export function assetUrl(sha256: string): Promise<string> {
  let p = cache.get(sha256);
  if (!p) {
    p = api.assetBlob(sha256).then((b) => URL.createObjectURL(b));
    p.catch(() => cache.delete(sha256));
    cache.set(sha256, p);
  }
  return p;
}

interface Props {
  sha256: string;
  alt: string;
  className?: string;
  onOpen?: (url: string) => void;
}

export function AuthImage({ sha256, alt, className = "", onOpen }: Props) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    setUrl(null);
    setFailed(false);
    assetUrl(sha256).then(
      (u) => alive && setUrl(u),
      () => alive && setFailed(true),
    );
    return () => {
      alive = false;
    };
  }, [sha256]);

  if (failed)
    return (
      <div className={`shot shot--missing ${className}`}>
        <ImageOff size={18} aria-hidden />
        <span>Screenshot unavailable</span>
      </div>
    );
  if (!url) return <div className={`shot shot--loading ${className}`} aria-label="Loading screenshot" />;
  const img = <img src={url} alt={alt} loading="lazy" />;
  return onOpen ? (
    <button type="button" className={`shot ${className}`} onClick={() => onOpen(url)} aria-label={`Enlarge: ${alt}`}>
      {img}
    </button>
  ) : (
    <div className={`shot ${className}`}>{img}</div>
  );
}

export function Lightbox({ url, alt, onClose }: { url: string; alt: string; onClose(): void }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  return (
    <div className="lightbox" role="dialog" aria-modal="true" aria-label={alt} onClick={onClose}>
      <img src={url} alt={alt} />
      <button className="btn btn--ghost lightbox__close" onClick={onClose} autoFocus>
        Close
      </button>
    </div>
  );
}
