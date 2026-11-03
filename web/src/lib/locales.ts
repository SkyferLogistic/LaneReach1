/** Countries, time zones and languages for pickers (names come from the browser's Intl data). */

const COUNTRY_CODES = (
  "AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR " +
  "BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC " +
  "EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK " +
  "HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB " +
  "LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ " +
  "NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU " +
  "RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN " +
  "TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS XK YE YT ZA ZM ZW"
).split(" ");

// ISO 639-1 languages used in freight outreach (contacts and offices use the base language).
const LANGUAGE_CODES = (
  "en ar zh es fr de pt it nl tr ru pl uk ro cs el hu sv da no fi he fa ur hi bn ta si ne " +
  "id ms th vi ko ja tl sw am ha yo az kk uz ka hy sr hr bg sk sl lt lv et af"
).split(" ");

// Fallback when the browser cannot list time zones (older Safari): the offices' usual ones.
const FALLBACK_ZONES = [
  "UTC",
  "America/Toronto",
  "America/New_York",
  "America/Chicago",
  "America/Los_Angeles",
  "America/Mexico_City",
  "America/Sao_Paulo",
  "Europe/London",
  "Europe/Paris",
  "Europe/Madrid",
  "Europe/Amsterdam",
  "Europe/Istanbul",
  "Asia/Dubai",
  "Asia/Riyadh",
  "Asia/Kolkata",
  "Asia/Colombo",
  "Asia/Singapore",
  "Asia/Shanghai",
  "Asia/Tokyo",
  "Africa/Lagos",
  "Africa/Johannesburg",
  "Australia/Sydney",
];

export interface Option {
  value: string;
  label: string;
  group?: string;
}

function displayNames(type: "region" | "language"): Intl.DisplayNames | null {
  try {
    return new Intl.DisplayNames(["en"], { type });
  } catch {
    return null;
  }
}

/** Every country, by English name ("Türkiye" style names as the browser spells them). */
export function countryOptions(): Option[] {
  const names = displayNames("region");
  return COUNTRY_CODES.map((code) => ({ value: code, label: names?.of(code) ?? code })).sort((a, b) =>
    a.label.localeCompare(b.label),
  );
}

export function languageOptions(): Option[] {
  const names = displayNames("language");
  return LANGUAGE_CODES.map((code) => ({ value: code, label: names?.of(code) ?? code })).sort((a, b) =>
    a.label.localeCompare(b.label),
  );
}

/** "+04:00" style offset of a zone right now ("" when the zone is unknown). */
export function zoneOffset(zone: string, at: Date = new Date()): string {
  try {
    const part = new Intl.DateTimeFormat("en-US", { timeZone: zone, timeZoneName: "longOffset" })
      .formatToParts(at)
      .find((item) => item.type === "timeZoneName")?.value;
    if (!part) return "";
    return part === "GMT" ? "+00:00" : part.replace("GMT", "");
  } catch {
    return "";
  }
}

/** Time zones grouped by region, labelled with their current UTC offset. */
export function timeZoneOptions(at: Date = new Date()): Option[] {
  const supported = (Intl as unknown as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf;
  let zones: string[] = FALLBACK_ZONES;
  try {
    if (supported) zones = supported("timeZone");
  } catch {
    zones = FALLBACK_ZONES;
  }
  if (!zones.includes("UTC")) zones = ["UTC", ...zones];
  return zones.map((zone) => {
    const [region, ...rest] = zone.split("/");
    const city = rest.join(" / ").replaceAll("_", " ") || zone;
    const offset = zoneOffset(zone, at);
    return { value: zone, label: `${city}${offset ? ` (UTC${offset})` : ""}`, group: rest.length ? region : "Other" };
  });
}

/** The browser's own time zone, when it has one. */
export function browserTimeZone(): string | null {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || null;
  } catch {
    return null;
  }
}
