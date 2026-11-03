/** Search-as-you-type contact selector. */
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, unwrap } from "../api/client";
import { keys } from "../api/queries";

export function useDebounced<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

export function ContactPicker({
  value,
  onChange,
  label = "Contact",
}: {
  value: string | null;
  onChange: (contactId: string | null) => void;
  label?: string;
}) {
  const [search, setSearch] = useState("");
  const q = useDebounced(search);
  const contacts = useQuery({
    queryKey: [...keys.contacts, "picker", q],
    queryFn: () =>
      unwrap(api.GET("/api/v1/contacts", { params: { query: { q: q || undefined, limit: 25 } } })),
  });
  return (
    <div className="stack tight">
      <label className="field">
        <span>{label}</span>
        <input
          className="input"
          placeholder="Search name, email or company domain…"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      </label>
      <select
        className="select"
        size={6}
        value={value ?? ""}
        aria-label="Matching contacts"
        onChange={(event) => onChange(event.target.value || null)}
      >
        {(contacts.data?.items ?? []).map((contact) => (
          <option key={contact.id} value={contact.id}>
            {contact.display_name} — {contact.email} {contact.country ? `(${contact.country})` : ""}
          </option>
        ))}
      </select>
      {!value && (contacts.data?.items.length ?? 0) > 0 && <span className="small muted">Click a contact in the list to choose them.</span>}
    </div>
  );
}
