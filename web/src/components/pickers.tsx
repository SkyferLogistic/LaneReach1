/** Dropdowns for countries, time zones and languages (with flags and UTC offsets). */
import { useMemo } from "react";
import { browserTimeZone, countryOptions, languageOptions, timeZoneOptions } from "../lib/locales";
import type { Option } from "../lib/locales";
import { countryFlag } from "../lib/freight";
import { SERVICE_LINES, SERVICE_LINE_LABELS } from "../lib/freight";
import { useOfferedLines } from "../api/queries";

interface PickerProps {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  id?: string;
  required?: boolean;
}

export function CountrySelect({ value, onChange, disabled, id, required, placeholder = "Choose a country" }: PickerProps & { placeholder?: string }) {
  const options = useMemo(countryOptions, []);
  return (
    <select id={id} className="select" value={value} disabled={disabled} required={required} onChange={(event) => onChange(event.target.value)}>
      <option value="">{placeholder}</option>
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {countryFlag(option.value)} {option.label} ({option.value})
        </option>
      ))}
    </select>
  );
}

export function LanguageSelect({ value, onChange, disabled, id, emptyLabel }: PickerProps & { emptyLabel?: string }) {
  const options = useMemo(languageOptions, []);
  const known = options.some((option) => option.value === value);
  return (
    <select id={id} className="select" value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)}>
      {emptyLabel !== undefined && <option value="">{emptyLabel}</option>}
      {!known && value && <option value={value}>{value}</option>}
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label} ({option.value})
        </option>
      ))}
    </select>
  );
}

export function TimeZoneSelect({
  value,
  onChange,
  disabled,
  id,
  required,
  emptyLabel = "Choose a time zone",
}: PickerProps & { emptyLabel?: string }) {
  const groups = useMemo(() => {
    const byGroup = new Map<string, Option[]>();
    for (const option of timeZoneOptions()) {
      const list = byGroup.get(option.group ?? "Other") ?? [];
      list.push(option);
      byGroup.set(option.group ?? "Other", list);
    }
    return [...byGroup.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, []);
  const local = browserTimeZone();
  const known = groups.some(([, options]) => options.some((option) => option.value === value));
  return (
    <div className="stack tight">
      <select id={id} className="select" value={value} disabled={disabled} required={required} onChange={(event) => onChange(event.target.value)}>
        <option value="">{emptyLabel}</option>
        {!known && value && <option value={value}>{value}</option>}
        {groups.map(([group, options]) => (
          <optgroup key={group} label={group}>
            {options.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
      {local && local !== value && !disabled && (
        <button type="button" className="btn ghost sm" style={{ alignSelf: "flex-start" }} onClick={() => onChange(local)}>
          Use this computer's zone ({local})
        </button>
      )}
    </div>
  );
}

/** `<option>`s for every service line (plus `any` when asked), for native selects. */
export function ServiceLineOptions({ any, current }: { any?: string; current?: string | null }) {
  const offered = useOfferedLines();
  return (
    <>
      {any && <option value="any">{any}</option>}
      {SERVICE_LINES.filter((line) => offered.includes(line) || line === current).map((line) => (
        <option key={line} value={line}>
          {SERVICE_LINE_LABELS[line]}
        </option>
      ))}
    </>
  );
}
