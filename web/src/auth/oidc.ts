// Minimal OIDC authorization-code + PKCE flow for a public SPA client.
// Isolated here so the rest of the app only sees "a bearer token".
//
// Flow: beginOidcLogin() → IdP → /auth/callback?code&state → completeOidcLogin()
// exchanges the code at the token endpoint and stores the id_token as the bearer.

const PENDING_KEY = "ws.oidc.pending";

interface Discovery {
  authorization_endpoint: string;
  token_endpoint: string;
}

interface Pending {
  state: string;
  nonce: string;
  verifier: string;
  issuer: string;
  client_id: string;
  return_to: string;
}

export function redirectUri(): string {
  return `${window.location.origin}/auth/callback`;
}

function b64url(bytes: ArrayBuffer | Uint8Array): string {
  const arr = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  let s = "";
  for (const b of arr) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function randomString(len = 32): string {
  return b64url(crypto.getRandomValues(new Uint8Array(len)));
}

async function challenge(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return b64url(digest);
}

async function discover(issuer: string): Promise<Discovery> {
  const url = issuer.replace(/\/+$/, "") + "/.well-known/openid-configuration";
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Couldn't load the sign-in configuration from ${issuer}.`);
  return res.json();
}

export async function beginOidcLogin(issuer: string, client_id: string, return_to = "/"): Promise<void> {
  const d = await discover(issuer);
  const p: Pending = { state: randomString(), nonce: randomString(), verifier: randomString(48), issuer, client_id, return_to };
  sessionStorage.setItem(PENDING_KEY, JSON.stringify(p));
  const params = new URLSearchParams({
    response_type: "code",
    client_id,
    redirect_uri: redirectUri(),
    scope: "openid email profile",
    state: p.state,
    nonce: p.nonce,
    code_challenge: await challenge(p.verifier),
    code_challenge_method: "S256",
  });
  window.location.assign(`${d.authorization_endpoint}?${params}`);
}

function decodeJwtPayload(jwt: string): Record<string, unknown> {
  const part = jwt.split(".")[1] ?? "";
  const json = atob(part.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(part.length / 4) * 4, "="));
  return JSON.parse(json);
}

/** Handles the redirect back from the IdP. Returns the id_token and where to go next. */
export async function completeOidcLogin(search: string): Promise<{ idToken: string; returnTo: string }> {
  const q = new URLSearchParams(search);
  const raw = sessionStorage.getItem(PENDING_KEY);
  sessionStorage.removeItem(PENDING_KEY);
  if (q.get("error")) throw new Error(q.get("error_description") || q.get("error") || "Sign-in was cancelled.");
  if (!raw) throw new Error("This sign-in link has expired. Start again from the sign-in page.");
  const p: Pending = JSON.parse(raw);
  if (q.get("state") !== p.state) throw new Error("Sign-in state didn't match. Start again from the sign-in page.");
  const code = q.get("code");
  if (!code) throw new Error("The identity provider didn't return a sign-in code.");

  const d = await discover(p.issuer);
  const res = await fetch(d.token_endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      code,
      redirect_uri: redirectUri(),
      client_id: p.client_id,
      code_verifier: p.verifier,
    }),
  });
  if (!res.ok) throw new Error("The identity provider rejected the sign-in code.");
  const tok = await res.json();
  const idToken: string | undefined = tok.id_token;
  if (!idToken) throw new Error("The identity provider didn't return an id_token.");
  // The server validates the signature via JWKS; we only check the nonce to bind the response to this browser.
  const claims = decodeJwtPayload(idToken);
  if (claims.nonce !== p.nonce) throw new Error("Sign-in response didn't match this browser session.");
  return { idToken, returnTo: p.return_to || "/" };
}
