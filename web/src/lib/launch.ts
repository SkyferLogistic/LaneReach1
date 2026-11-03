/** The launch checklist: what is still missing before outreach can run for real. */

export interface LaunchInputs {
  library?: { provider: string; client_secret_configured: boolean; last_sync_status: string };
  agents?: { service_line: string; is_active: boolean }[];
  matrix?: {
    languages: { code: string }[];
    scripts: { versions: Record<string, { status: string }> }[];
  };
  banners?: number;
  branding?: { company_name?: string | null; logo_asset_id?: string | null };
  publishedWorkflows?: number;
  countries?: number;
}

export interface LaunchStep {
  id: string;
  title: string;
  detail: string;
  done: boolean;
  to: string;
}

/** The steps, in the order they are best done; unknown inputs count as not done. */
export function launchSteps(inputs: LaunchInputs): LaunchStep[] {
  const library = inputs.library;
  const connected = library?.provider === "sharepoint" && library.client_secret_configured && ["ok", "partial"].includes(library.last_sync_status);
  const active = (inputs.agents ?? []).filter((agent) => agent.is_active);
  const air = active.filter((agent) => agent.service_line === "air").length;
  const ocean = active.filter((agent) => agent.service_line === "ocean").length;
  const languages = inputs.matrix?.languages.map((language) => language.code) ?? [];
  const live = new Set(
    (inputs.matrix?.scripts ?? []).flatMap((script) =>
      Object.entries(script.versions)
        .filter(([, version]) => version.status === "active")
        .map(([code]) => code),
    ),
  );
  const covered = languages.filter((code) => live.has(code)).length;
  const branded = Boolean(inputs.branding?.company_name && inputs.branding.logo_asset_id);
  return [
    {
      id: "sharepoint",
      title: "Connect the SharePoint library",
      detail: connected
        ? "Synced with SharePoint."
        : library?.provider === "sharepoint"
          ? "SharePoint is selected; the secret or the first sync is missing."
          : "Using the local folder. Add the Azure app details in Settings.",
      done: connected,
      to: "/settings?tab=library",
    },
    {
      id: "countries",
      title: "Countries & destination phrases",
      detail: inputs.countries ? `${inputs.countries} countries mapped to a script language.` : "Add the countries you email.",
      done: (inputs.countries ?? 0) > 0,
      to: "/countries",
    },
    {
      id: "agents",
      title: "Air and ocean agents",
      detail: `${air} air · ${ocean} ocean agents active.`,
      done: air > 0 && ocean > 0,
      to: "/agents",
    },
    {
      id: "scripts",
      title: "A script in every language",
      detail: languages.length ? `${covered} of ${languages.length} languages have a live script.` : "No languages yet.",
      done: languages.length > 0 && covered === languages.length,
      to: "/scripts",
    },
    {
      id: "banners",
      title: "Country banners",
      detail: inputs.banners ? `${inputs.banners} banner${inputs.banners === 1 ? "" : "s"} in the library.` : "Add a default banner for air and ocean.",
      done: (inputs.banners ?? 0) > 0,
      to: "/scripts",
    },
    {
      id: "branding",
      title: "Signature branding and logo",
      detail: branded ? "Logo and company details set." : "Add the logo (Brand/logo.png) and company details.",
      done: branded,
      to: "/settings?tab=branding",
    },
    {
      id: "workflow",
      title: "Publish an outreach workflow",
      detail: inputs.publishedWorkflows ? `${inputs.publishedWorkflows} published.` : "Build a sequence on the canvas and publish it.",
      done: (inputs.publishedWorkflows ?? 0) > 0,
      to: "/workflows",
    },
  ];
}
