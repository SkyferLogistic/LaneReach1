/**
 * Countries & languages: which script language each destination country gets, and how the
 * country is written in it (`{to_country}`). Marketing can add or change countries at any time.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api, unwrap } from "../api/client";
import type { Schemas } from "../api/client";
import { keys, useCountries, useScriptMatrix } from "../api/queries";
import { useSession } from "../auth/session";
import { Icon } from "../components/icons";
import { Page } from "../components/Layout";
import { SkeletonRows } from "../components/motion";
import { Badge, Button, Card, Empty, ErrorAlert, Modal, PageHeader, Switch, useErrorToast, useToast } from "../components/ui";
import { countryFlag } from "../lib/freight";

type Country = Schemas["CountryResponse"];

interface LanguageGroup {
  code: string;
  name: string;
  countries: Country[];
  live: boolean;
}

function toRequest(country: Country): Schemas["CountryRequest"] {
  return {
    name: country.name,
    language: country.language,
    to_country_phrase: country.to_country_phrase,
    allow_language_fallback: country.allow_language_fallback,
    is_enabled: country.is_enabled,
  };
}

export function CountriesPage() {
  const countries = useCountries();
  const matrix = useScriptMatrix();
  const { can } = useSession();
  const writer = can("workflows:write");
  const [editing, setEditing] = useState<Country | "new" | null>(null);
  const [search, setSearch] = useState("");
  const toast = useToast();
  const onError = useErrorToast();
  const queryClient = useQueryClient();

  const liveLanguages = useMemo(() => {
    const live = new Set<string>();
    for (const script of matrix.data?.scripts ?? []) {
      for (const version of Object.values(script.versions)) if (version.status === "active") live.add(version.language);
    }
    return live;
  }, [matrix.data]);

  const groups = useMemo<LanguageGroup[]>(() => {
    const needle = search.trim().toLowerCase();
    const map = new Map<string, LanguageGroup>();
    for (const country of countries.data ?? []) {
      if (needle && !`${country.name} ${country.country} ${country.language_name} ${country.to_country_phrase}`.toLowerCase().includes(needle)) continue;
      const group = map.get(country.language) ?? { code: country.language, name: country.language_name, countries: [], live: liveLanguages.has(country.language) };
      group.countries.push(country);
      map.set(country.language, group);
    }
    return [...map.values()].sort((a, b) => b.countries.length - a.countries.length || a.name.localeCompare(b.name));
  }, [countries.data, search, liveLanguages]);

  const update = useMutation({
    mutationFn: ({ code, body }: { code: string; body: Schemas["CountryRequest"] }) =>
      unwrap(api.PUT("/api/v1/countries/{country}", { params: { path: { country: code } }, body })),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: keys.countries }),
    onError,
  });
  const remove = useMutation({
    mutationFn: (code: string) => unwrap(api.DELETE("/api/v1/countries/{country}", { params: { path: { country: code } } })),
    onSuccess: () => {
      toast("Country removed. Its contacts now use their own language.");
      void queryClient.invalidateQueries({ queryKey: keys.countries });
    },
    onError,
  });

  const total = countries.data?.length ?? 0;
  const languages = new Set((countries.data ?? []).map((country) => country.language)).size;

  return (
    <Page>
      <PageHeader
        eyebrow={<><Icon name="globe" /> {total} countries · {languages} script languages</>}
        title="Countries & languages"
        description="Each destination country gets the script in its language, with the country written the way that language says it. Countries without a live script fall back to another language unless they are held."
        actions={
          <>
            <input className="input" style={{ width: 220 }} placeholder="Find a country or language…" value={search} onChange={(event) => setSearch(event.target.value)} aria-label="Find a country" />
            {writer && (
              <Button variant="primary" onClick={() => setEditing("new")}>
                <Icon name="plus" /> Add country
              </Button>
            )}
          </>
        }
      />
      {countries.isLoading && <SkeletonRows rows={6} />}
      <ErrorAlert error={countries.error} />
      {countries.data && groups.length === 0 && (
        <Empty title={total ? "Nothing matches" : "No countries yet"}>{total ? "Try another search." : "Add the destination countries you email."}</Empty>
      )}
      <div className="grid" style={{ alignItems: "start", gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 480px), 1fr))" }}>
        {groups.map((group) => (
          <Card
            key={group.code}
            title={
              <div className="row">
                <h2>{group.name}</h2>
                <Badge tone="blue">{group.code}</Badge>
                <span className="small muted">{group.countries.length} {group.countries.length === 1 ? "country" : "countries"}</span>
              </div>
            }
            actions={
              group.live ? (
                <Badge tone="green"><Icon name="check" /> Script live</Badge>
              ) : group.countries.some((country) => !country.allow_language_fallback) ? (
                <Badge tone="amber"><Icon name="pause" /> Held — no script</Badge>
              ) : (
                <Badge><Icon name="translate" /> Falls back</Badge>
              )
            }
            flush
          >
            <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Country</th>
                  <th>{"{to_country}"}</th>
                  <th title="When off, emails are held until a script exists in this language">Fallback</th>
                  <th>On</th>
                  {writer && <th />}
                </tr>
              </thead>
              <tbody>
                {group.countries.map((country) => (
                  <tr key={country.country} style={{ opacity: country.is_enabled ? 1 : 0.55 }}>
                    <td className="nowrap">
                      <span style={{ fontSize: 17, marginRight: 6 }}>{countryFlag(country.country)}</span>
                      {country.name} <span className="muted small">{country.country}</span>
                    </td>
                    <td dir="auto" className="nowrap">“{country.to_country_phrase}”</td>
                    <td>
                      <Switch
                        label={`Fallback for ${country.name}`}
                        checked={country.allow_language_fallback}
                        disabled={!writer || update.isPending}
                        onChange={(checked) => update.mutate({ code: country.country, body: { ...toRequest(country), allow_language_fallback: checked } })}
                      />
                    </td>
                    <td>
                      <Switch
                        label={`Use the table for ${country.name}`}
                        checked={country.is_enabled}
                        disabled={!writer || update.isPending}
                        onChange={(checked) => update.mutate({ code: country.country, body: { ...toRequest(country), is_enabled: checked } })}
                      />
                    </td>
                    {writer && (
                      <td className="nowrap">
                        <Button size="sm" variant="ghost" onClick={() => setEditing(country)}>Edit</Button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          </Card>
        ))}
      </div>
      {editing && (
        <CountryModal
          country={editing === "new" ? null : editing}
          languages={(countries.data ?? []).reduce<Map<string, string>>((map, country) => map.set(country.language, country.language_name), new Map())}
          onClose={() => setEditing(null)}
          onDelete={
            editing !== "new"
              ? () => {
                  if (window.confirm(`Remove ${editing.name} from the table?`)) {
                    remove.mutate(editing.country);
                    setEditing(null);
                  }
                }
              : undefined
          }
        />
      )}
    </Page>
  );
}

function CountryModal({
  country,
  languages,
  onClose,
  onDelete,
}: {
  country: Country | null;
  languages: Map<string, string>;
  onClose: () => void;
  onDelete?: () => void;
}) {
  const [code, setCode] = useState(country?.country ?? "");
  const [name, setName] = useState(country?.name ?? "");
  const [language, setLanguage] = useState(country?.language ?? "en");
  const [phrase, setPhrase] = useState(country?.to_country_phrase ?? "");
  const [fallback, setFallback] = useState(country?.allow_language_fallback ?? true);
  const [enabled, setEnabled] = useState(country?.is_enabled ?? true);
  const toast = useToast();
  const queryClient = useQueryClient();
  const save = useMutation({
    mutationFn: () =>
      unwrap(
        api.PUT("/api/v1/countries/{country}", {
          params: { path: { country: code.toUpperCase() } },
          body: { name: name.trim(), language, to_country_phrase: phrase.trim(), allow_language_fallback: fallback, is_enabled: enabled },
        }),
      ),
    onSuccess: (saved) => {
      toast(`${saved.name} saved (${saved.language_name}).`);
      void queryClient.invalidateQueries({ queryKey: keys.countries });
      void queryClient.invalidateQueries({ queryKey: keys.scripts });
      onClose();
    },
  });
  const validCode = /^[A-Za-z]{2}$/.test(code);
  const validLanguage = /^[a-z]{2}(-[a-z0-9]{2,4})?$/.test(language);
  return (
    <Modal
      title={country ? `${countryFlag(country.country)} ${country.name}` : "Add a country"}
      onClose={onClose}
      footer={
        <>
          {onDelete && (
            <Button variant="danger" onClick={onDelete} style={{ marginRight: "auto" }}>
              Remove
            </Button>
          )}
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={save.isPending} disabled={!validCode || !name.trim() || !validLanguage || !phrase.trim()} onClick={() => save.mutate()}>
            Save
          </Button>
        </>
      }
    >
      <div className="stack">
        <div className="form-grid">
          <label className="field">
            <span>ISO code</span>
            <input className="input" maxLength={2} disabled={Boolean(country)} value={code} onChange={(event) => setCode(event.target.value.toUpperCase())} placeholder="TR" />
          </label>
          <label className="field">
            <span>Name (English)</span>
            <input className="input" value={name} onChange={(event) => setName(event.target.value)} placeholder="Turkey" />
          </label>
        </div>
        <label className="field">
          <span>Script language</span>
          <input className="input" list="country-languages" value={language} onChange={(event) => setLanguage(event.target.value.trim().toLowerCase())} placeholder="tr, pt-br, zh-hant…" />
          <datalist id="country-languages">
            {[...languages].map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </datalist>
          <span className="field-help">A language code, optionally with a variant (pt-br, zh-hant, en-na). A new code becomes a new column in Scripts.</span>
        </label>
        <label className="field">
          <span>{"{to_country}"} phrase, in that language</span>
          <input className="input" dir="auto" value={phrase} onChange={(event) => setPhrase(event.target.value)} placeholder="Türkiye'ye" />
        </label>
        <div className="email-preview small" dir="auto">
          …from the USA or Canada <strong>{phrase || "{to_country}"}</strong>
        </div>
        <label className="checkbox">
          <Switch checked={fallback} onChange={setFallback} label="Fallback" /> Fall back to another language when this one has no script
        </label>
        {!fallback && <div className="alert warning small">Emails to {name || "this country"} are held until a script exists in {languages.get(language) ?? language}.</div>}
        <label className="checkbox">
          <Switch checked={enabled} onChange={setEnabled} label="Enabled" /> Use this row (off = contacts use their own language)
        </label>
        <ErrorAlert error={save.error} />
      </div>
    </Modal>
  );
}
