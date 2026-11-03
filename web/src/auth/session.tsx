/**
 * Session: who is signed in, to which tenant, with which scopes.
 *
 * Dev mode signs in with a dev token (tenant picker). The token is kept in localStorage so a
 * reload keeps you signed in; storage can be unavailable (private windows), so every access is
 * guarded and the app still works for the current tab.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api, setApiToken, setUnauthorizedHandler, unwrap } from "../api/client";
import type { Schemas } from "../api/client";

const STORAGE_KEY = "lanereach.session";

export interface Session {
  token: string;
  tenantId: string;
  tenantName: string;
  subject: string;
  roles: string[];
  scopes: string[];
  expiresAt: string;
}

interface SessionContextValue {
  session: Session | null;
  signIn: (tenant: Schemas["DevTenantResponse"], subject: string, roles: string[]) => Promise<void>;
  /** Company sign-in: an access token from the identity provider. */
  signInWithToken: (accessToken: string, expiresInSeconds: number) => Promise<void>;
  signOut: () => void;
  can: (scope: string) => boolean;
}

const SessionContext = createContext<SessionContextValue | null>(null);

function readStoredSession(): Session | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const session = JSON.parse(raw) as Session;
    return new Date(session.expiresAt).getTime() > Date.now() ? session : null;
  } catch {
    return null;
  }
}

function storeSession(session: Session | null): void {
  try {
    if (session) window.localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
    else window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Storage unavailable: the session lives for this tab only.
  }
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(() => {
    const stored = readStoredSession();
    setApiToken(stored?.token ?? null);
    return stored;
  });

  const signOut = useCallback(() => {
    setApiToken(null);
    storeSession(null);
    setSession(null);
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(signOut);
    return () => setUnauthorizedHandler(null);
  }, [signOut]);

  const signIn = useCallback(
    async (tenant: Schemas["DevTenantResponse"], subject: string, roles: string[]) => {
      const token = await unwrap(
        api.POST("/api/v1/auth/dev-token", {
          body: { tenant_id: tenant.id, subject, roles: roles as Schemas["Role"][], expires_in_seconds: 12 * 3600 },
        }),
      );
      setApiToken(token.access_token);
      const me = await unwrap(api.GET("/api/v1/auth/me"));
      const next: Session = {
        token: token.access_token,
        tenantId: tenant.id,
        tenantName: tenant.name,
        subject: me.subject,
        roles: me.roles,
        scopes: me.scopes,
        expiresAt: token.expires_at,
      };
      storeSession(next);
      setSession(next);
    },
    [],
  );

  const signInWithToken = useCallback(async (accessToken: string, expiresInSeconds: number) => {
    setApiToken(accessToken);
    try {
      const me = await unwrap(api.GET("/api/v1/auth/me"));
      const next: Session = {
        token: accessToken,
        tenantId: me.tenant_id,
        tenantName: me.tenant_name ?? "",
        subject: me.subject,
        roles: me.roles,
        scopes: me.scopes,
        expiresAt: new Date(Date.now() + expiresInSeconds * 1000).toISOString(),
      };
      storeSession(next);
      setSession(next);
    } catch (error) {
      setApiToken(null);
      throw error;
    }
  }, []);

  const value = useMemo<SessionContextValue>(
    () => ({
      session,
      signIn,
      signInWithToken,
      signOut,
      can: (scope: string) => Boolean(session?.scopes.includes(scope)),
    }),
    [session, signIn, signInWithToken, signOut],
  );
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionContextValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession must be used inside SessionProvider");
  return value;
}
