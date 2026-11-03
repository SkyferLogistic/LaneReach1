/**
 * Company sign-in (OIDC Authorization Code + PKCE, e.g. Microsoft Entra ID) for a single-page app.
 *
 * The console never holds a client secret: it proves the code exchange with a one-time
 * verifier. `state` protects against cross-site login; both are kept in sessionStorage for the
 * round trip only.
 */

export interface OidcConfig {
  authority: string;
  clientId: string;
  scopes: string[];
}

export interface PendingLogin {
  state: string;
  verifier: string;
  returnTo: string;
}

const PENDING_KEY = "lanereach.oidc.pending";

function base64Url(bytes: Uint8Array): string {
  let text = "";
  for (const byte of bytes) text += String.fromCharCode(byte);
  return btoa(text).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

/** A random URL-safe string (verifier, state). */
export function randomToken(bytes = 32, source: Pick<Crypto, "getRandomValues"> = crypto): string {
  return base64Url(source.getRandomValues(new Uint8Array(bytes)));
}

/** S256 code challenge of a verifier. */
export async function codeChallenge(verifier: string, subtle: SubtleCrypto = crypto.subtle): Promise<string> {
  // Verifiers are ASCII (RFC 7636), so their bytes are their char codes.
  const bytes = Uint8Array.from(verifier, (character) => character.charCodeAt(0));
  const digest = await subtle.digest("SHA-256", bytes);
  return base64Url(new Uint8Array(digest));
}

function endpoint(authority: string, path: "authorize" | "token"): string {
  const base = authority.replace(/\/+$/, "");
  // Entra ID v2 and most providers: <authority>/oauth2/v2.0/<path> or <authority>/<path>.
  return base.includes("login.microsoftonline.com") ? `${base.replace(/\/v2\.0$/, "")}/oauth2/v2.0/${path}` : `${base}/${path}`;
}

/** The URL to send the browser to. */
export async function authorizeUrl(config: OidcConfig, redirectUri: string, pending: PendingLogin): Promise<string> {
  const params = new URLSearchParams({
    client_id: config.clientId,
    response_type: "code",
    redirect_uri: redirectUri,
    scope: config.scopes.join(" "),
    state: pending.state,
    code_challenge: await codeChallenge(pending.verifier),
    code_challenge_method: "S256",
    response_mode: "query",
  });
  return `${endpoint(config.authority, "authorize")}?${params.toString()}`;
}

/** Read `code` and `state` from the callback; an error from the provider is returned as text. */
export function readCallback(search: string): { code: string; state: string } | { error: string } {
  const params = new URLSearchParams(search);
  const error = params.get("error_description") ?? params.get("error");
  if (error) return { error };
  const code = params.get("code");
  const state = params.get("state");
  return code && state ? { code, state } : { error: "The sign-in answer is incomplete." };
}

/** Exchange the code for tokens at the provider (public client: no secret). */
export async function redeemCode(
  config: OidcConfig,
  redirectUri: string,
  code: string,
  verifier: string,
  fetcher: typeof fetch = fetch,
): Promise<{ accessToken: string; expiresIn: number }> {
  const response = await fetcher(endpoint(config.authority, "token"), {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      client_id: config.clientId,
      grant_type: "authorization_code",
      code,
      redirect_uri: redirectUri,
      code_verifier: verifier,
      scope: config.scopes.join(" "),
    }),
  });
  const data = (await response.json()) as { access_token?: string; expires_in?: number; error_description?: string };
  if (!response.ok || !data.access_token) throw new Error(data.error_description ?? "The sign-in could not be completed.");
  return { accessToken: data.access_token, expiresIn: Number(data.expires_in ?? 3600) };
}

export function savePending(pending: PendingLogin, storage: Pick<Storage, "setItem"> = sessionStorage): void {
  storage.setItem(PENDING_KEY, JSON.stringify(pending));
}

/** The pending login, removed as it is read (a callback can be used once). */
export function takePending(storage: Pick<Storage, "getItem" | "removeItem"> = sessionStorage): PendingLogin | null {
  const raw = storage.getItem(PENDING_KEY);
  storage.removeItem(PENDING_KEY);
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as PendingLogin;
    return typeof value.state === "string" && typeof value.verifier === "string" ? value : null;
  } catch {
    return null;
  }
}
