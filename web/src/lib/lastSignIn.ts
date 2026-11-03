/**
 * The last development sign-in (company, email, role), remembered per browser so the login opens
 * where the person left. A convenience only: storage may be unavailable and then nothing is kept.
 */

export interface LastSignIn {
  tenantId: string;
  subject: string;
  role: string;
}

const KEY = "lanereach.last-sign-in";

export function readLastSignIn(storage: Storage | undefined = safeStorage()): LastSignIn | null {
  try {
    const raw = storage?.getItem(KEY);
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<LastSignIn>;
    if (typeof value.tenantId !== "string" || typeof value.subject !== "string" || typeof value.role !== "string") return null;
    return { tenantId: value.tenantId, subject: value.subject, role: value.role };
  } catch {
    return null;
  }
}

export function saveLastSignIn(value: LastSignIn, storage: Storage | undefined = safeStorage()): void {
  try {
    storage?.setItem(KEY, JSON.stringify(value));
  } catch {
    // Private mode or blocked storage: the next login simply starts from the defaults.
  }
}

function safeStorage(): Storage | undefined {
  try {
    return window.localStorage;
  } catch {
    return undefined;
  }
}

export const ROLE_NAMES: Record<string, string> = {
  admin: "Admin",
  campaign_manager: "Campaign manager",
  office_owner: "Office owner",
  pricing: "Pricing",
  reviewer: "Reviewer",
  viewer: "Viewer",
};

/** "Office owner, Pricing" from role ids. */
export function roleNames(roles: string[]): string {
  return roles.map((role) => ROLE_NAMES[role] ?? role.replace(/_/g, " ")).join(", ") || "no role";
}
