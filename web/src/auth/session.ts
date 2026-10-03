// Bearer token storage. In dev mode this is the opaque dev-login token;
// in OIDC mode it is the id_token returned by the identity provider.
const KEY = "ws.token";

export function getToken(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string): void {
  try {
    localStorage.setItem(KEY, token);
  } catch {
    /* storage unavailable */
  }
}

export function clearToken(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* storage unavailable */
  }
}
