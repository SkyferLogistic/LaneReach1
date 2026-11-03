/**
 * Landing-page data and maths: the lanes on the hero map, the scripted inbox demo, the comparison
 * table, AI task labels and the AI orbit, the AI co-pilot examples (lead score, weekly digest,
 * quiet deals), the reply journey, integrations, FAQ and contact-form checks. Kept free of React so
 * it is unit-tested.
 */

export type Point = [number, number];

/** A quadratic arc from `a` to `b`, lifted perpendicular to the chord by `lift` × its length. */
export function arcPath(a: Point, b: Point, lift = 0.22): string {
  const [x1, y1] = a;
  const [x2, y2] = b;
  const mx = (x1 + x2) / 2;
  const my = (y1 + y2) / 2;
  const dx = x2 - x1;
  const dy = y2 - y1;
  const length = Math.hypot(dx, dy) || 1;
  // Perpendicular pointing "up" on screen so arcs bow like flight paths.
  let nx = -dy / length;
  let ny = dx / length;
  if (ny > 0) {
    nx = -nx;
    ny = -ny;
  }
  const cx = mx + nx * length * lift;
  const cy = my + ny * length * lift;
  return `M${x1.toFixed(1)},${y1.toFixed(1)} Q${cx.toFixed(1)},${cy.toFixed(1)} ${x2.toFixed(1)},${y2.toFixed(1)}`;
}

export interface HeroLane {
  from: string;
  to: string;
  fromAt: [number, number];
  toAt: [number, number];
  mode: "ocean" | "air" | "road" | "rail";
  label: string;
}

/** Example lanes for the hero map (illustrative, not customer data). */
export const HERO_LANES: HeroLane[] = [
  { from: "Chennai", to: "Jebel Ali", fromAt: [80.27, 13.08], toAt: [55.03, 25.01], mode: "ocean", label: "2 × 40HC · rate request" },
  { from: "Shanghai", to: "Rotterdam", fromAt: [121.47, 31.23], toAt: [4.48, 51.92], mode: "ocean", label: "FCL · quote drafted" },
  { from: "Toronto", to: "Istanbul", fromAt: [-79.38, 43.65], toAt: [28.98, 41.01], mode: "air", label: "Air · 420 kg" },
  { from: "Hamburg", to: "Riyadh", fromAt: [9.99, 53.55], toAt: [46.72, 24.71], mode: "road", label: "FTL · reply in German" },
  { from: "Santos", to: "Valencia", fromAt: [-46.33, -23.96], toAt: [-0.38, 39.47], mode: "ocean", label: "Reefer · follow-up" },
  { from: "Singapore", to: "Sydney", fromAt: [103.82, 1.35], toAt: [151.21, -33.87], mode: "air", label: "Express · meeting booked" },
  { from: "Xi'an", to: "Duisburg", fromAt: [108.94, 34.34], toAt: [6.76, 51.43], mode: "rail", label: "Rail · new lead" },
];

/** Ports that scroll by in the lane ticker. */
export const TICKER = [
  "INMAA → AEJEA · 40HC",
  "CNSHA → NLRTM · FCL",
  "YYZ → IST · air 420 kg",
  "DEHAM → SARUH · FTL",
  "BRSSZ → ESVLC · reefer",
  "SIN → SYD · express",
  "CNXIY → DEDUI · rail",
  "INNSA → GBFXT · LCL 6 cbm",
  "DXB → JFK · air 1.2 t",
  "KRPUS → USLAX · 2 × 40HC",
];

// --- scripted inbox demo -----------------------------------------------------------------------

export interface DemoField {
  label: string;
  value: string;
  /** The words in the email the value comes from (highlighted). */
  source: string;
}

export interface DemoScenario {
  id: string;
  tab: string;
  from: string;
  company: string;
  language: string;
  subject: string;
  body: string;
  bucket: string;
  confidence: number;
  fields: DemoField[];
  outcome: string;
  outcomeTone: "good" | "warn" | "stop";
  steps: string[];
  reply: string | null;
}

export const DEMO_SCENARIOS: DemoScenario[] = [
  {
    id: "rate",
    tab: "Rate request",
    from: "Ahmed K.",
    company: "Gulf Importers",
    language: "English",
    subject: "Re: Shipping to the Gulf",
    body: "Hi Sara, thanks for reaching out. Please quote 2x40HC from Chennai to Jebel Ali, FOB, cargo ready 15 Oct. Commodity is ceramic tiles.",
    bucket: "Rate request",
    confidence: 0.94,
    fields: [
      { label: "Origin", value: "Chennai · INMAA", source: "Chennai" },
      { label: "Destination", value: "Jebel Ali · AEJEA", source: "Jebel Ali" },
      { label: "Equipment", value: "2 × 40HC", source: "2x40HC" },
      { label: "Incoterm", value: "FOB", source: "FOB" },
      { label: "Ready", value: "15 Oct", source: "15 Oct" },
      { label: "Commodity", value: "Ceramic tiles", source: "ceramic tiles" },
    ],
    outcome: "Deal opened · quote drafted from your rate sheet: 3,040.00 USD",
    outcomeTone: "good",
    steps: ["Reply read", "Shipment extracted", "Deal opened", "Rates matched", "Quote drafted for review"],
    reply: "Thank you Ahmed. Our pricing team is preparing your quote for 2 × 40HC Chennai → Jebel Ali.",
  },
  {
    id: "ooo",
    tab: "Out of office",
    from: "Elif Y.",
    company: "Anadolu Lojistik",
    language: "Turkish",
    subject: "Otomatik yanıt: Shipping to Türkiye",
    body: "Merhaba, 20 Ekim'e kadar ofis dışındayım. Acil konular için operasyon ekibimize yazabilirsiniz.",
    bucket: "Out of office",
    confidence: 0.97,
    fields: [{ label: "Back on", value: "20 Oct", source: "20 Ekim" }],
    outcome: "Sequence paused · follow-up moved to 21 Oct",
    outcomeTone: "warn",
    steps: ["Reply read", "Return date found", "Follow-up rescheduled"],
    reply: null,
  },
  {
    id: "stop",
    tab: "Unsubscribe",
    from: "J. Miller",
    company: "Northline Trading",
    language: "English",
    subject: "Re: Freight rates to Canada",
    body: "Please remove me from your mailing list. We have a forwarder already.",
    bucket: "Unsubscribe",
    confidence: 0.99,
    fields: [],
    outcome: "Address suppressed · every workflow stops for this contact",
    outcomeTone: "stop",
    steps: ["Reply read", "Opt-out detected", "Suppressed everywhere"],
    reply: null,
  },
];

/** The scenario at a position (wrapping around). */
export function scenarioAt(index: number): DemoScenario {
  const count = DEMO_SCENARIOS.length;
  const scenario = DEMO_SCENARIOS[((index % count) + count) % count];
  if (!scenario) throw new Error("No demo scenarios");
  return scenario;
}

/** Split an email body into text and highlighted parts for the extracted sources. */
export function highlightSources(body: string, sources: string[]): { text: string; mark: boolean }[] {
  const wanted = sources.filter(Boolean).sort((a, b) => b.length - a.length);
  if (wanted.length === 0) return [{ text: body, mark: false }];
  const escaped = wanted.map((source) => source.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const pattern = new RegExp(`(${escaped.join("|")})`, "gi");
  return body
    .split(pattern)
    .filter((part) => part !== "")
    .map((part) => ({ text: part, mark: wanted.some((source) => source.toLowerCase() === part.toLowerCase()) }));
}

// --- AI ---------------------------------------------------------------------------------------

export const AI_TASK_LABELS: Record<string, { title: string; detail: string }> = {
  classify_reply: { title: "Reads every reply", detail: "Rate request, question, meeting, not interested, out of office, unsubscribe, bounce, in any language." },
  extract_shipment_details: { title: "Pulls out the shipment", detail: "Origin and destination with port codes, mode, containers, weight, Incoterm, ready date, and what is still missing." },
  draft_reply: { title: "Drafts the answer", detail: "In the office's voice and the customer's language, waiting for a person unless your policy says otherwise." },
  localize_template: { title: "Localises your scripts", detail: "Adapts approved templates to a language and region, flagged for review." },
  match_lane: { title: "Matches lanes to your rates", detail: "Maps “Jebel Ali free zone” or “Madras” onto the places in your rate sheets, never inventing one." },
  answer_help_question: { title: "Answers how-to questions", detail: "From the help articles only, with sources, and says so when LaneReach does not do something." },
  draft_workflow: { title: "Drafts workflows", detail: "Turns a sentence into a workflow draft you review on the canvas; never publishes it." },
  assess_lead: { title: "Scores every lead", detail: "A 0–100 score per deal from the reply's intent, how complete the shipment is, how fast they answered, engagement and company history, with the reasons in plain words." },
  write_digest: { title: "Writes your weekly digest", detail: "Lanes heating up, win and loss by lane and offices answering slowly, summed up in a few sentences from the week's numbers." },
  suggest_follow_up: { title: "Suggests follow-ups for quiet deals", detail: "When a deal has been quiet for a few days, proposes the next step and drafts a follow-up for a person to review." },
};

/** The AI skills being built now; shown as "Coming soon" until the running version lists them. */
export const UPCOMING_AI_TASKS = ["assess_lead", "write_digest", "suggest_follow_up"] as const;
export type UpcomingAiTask = (typeof UPCOMING_AI_TASKS)[number];

/** Whether an AI skill is in the running version (`GET /site` → ai_tasks) or still coming. */
export function aiTaskStatus(task: string, liveTasks: readonly string[]): "live" | "coming" {
  return liveTasks.includes(task) ? "live" : "coming";
}

/** True once the running version lists every one of the new AI skills (copy then stops saying "coming soon"). */
export function upcomingAiLive(liveTasks: readonly string[]): boolean {
  return UPCOMING_AI_TASKS.every((task) => liveTasks.includes(task));
}

export interface OrbitSkill {
  id: string;
  title: string;
  detail: string;
  live: boolean;
  /** 0 = outer ring (skills in the running version), 1 = inner ring (skills still coming). */
  ring: 0 | 1;
}

/** Every AI skill for the orbit: the running version's skills (outer ring), then the ones still coming (inner ring). */
export function orbitSkills(liveTasks: readonly string[]): OrbitSkill[] {
  const live = describeAiTasks(liveTasks.filter((task, position) => liveTasks.indexOf(task) === position)).map((task) => ({ ...task, live: true, ring: 0 as const }));
  const coming = UPCOMING_AI_TASKS.filter((task) => !liveTasks.includes(task)).map((id) => ({ id, ...(AI_TASK_LABELS[id] ?? { title: id, detail: "" }), live: false, ring: 1 as const }));
  return [...live, ...coming];
}

/** Where the `index`-th of `count` items sits on a circle around (50, 50), in percent; the first is at 12 o'clock. */
export function orbitPosition(index: number, count: number, radius: number, offsetDegrees = 0): Point {
  const angle = ((count > 0 ? index / count : 0) * 360 + offsetDegrees - 90) * (Math.PI / 180);
  const round = (value: number) => Math.round(value * 100) / 100;
  return [round(50 + Math.cos(angle) * radius), round(50 + Math.sin(angle) * radius)];
}

// --- AI co-pilot examples (illustrative, labelled "Example" on the page) ------------------------

export interface ScoreReason {
  points: number;
  text: string;
  /** Which signal the reason comes from. */
  signal: "Intent" | "Details" | "Speed" | "Engagement" | "History";
}

export const LEAD_SCORE_EXAMPLE = {
  company: "Gulf Importers",
  contact: "Ahmed K.",
  lane: "Chennai → Jebel Ali",
  load: "2 × 40HC · FOB · ceramic tiles",
  base: 40,
  reasons: [
    { points: 18, text: "Asked for a quote with the full lane", signal: "Intent" },
    { points: 12, text: "Containers, Incoterm and commodity given", signal: "Details" },
    { points: 9, text: "Replied within 2 hours", signal: "Speed" },
    { points: 5, text: "Second reply in this thread", signal: "Engagement" },
    { points: 4, text: "Quoted with you before", signal: "History" },
    { points: -6, text: "No ready date yet", signal: "Details" },
  ] satisfies ScoreReason[],
};

/** A lead score from a starting value and the reasons' points, kept between 0 and 100. */
export function scoreLead(base: number, reasons: readonly Pick<ScoreReason, "points">[]): number {
  const total = reasons.reduce((sum, reason) => sum + reason.points, base);
  return Math.max(0, Math.min(100, Math.round(total)));
}

/** A word for a 0–100 score. */
export function scoreBand(score: number): { label: "Hot" | "Warm" | "Cool"; tone: "good" | "warn" | "info" } {
  if (score >= 75) return { label: "Hot", tone: "good" };
  if (score >= 50) return { label: "Warm", tone: "warn" };
  return { label: "Cool", tone: "info" };
}

export interface DigestLane {
  lane: string;
  mode: "ocean" | "air" | "road" | "rail";
  /** Quote requests per week, oldest first. */
  weeks: number[];
  won: number;
  quoted: number;
}

export const DIGEST_EXAMPLE = {
  week: "Week 44",
  headline:
    "Chennai → Jebel Ali is heating up: 12 quote requests this week, twice its usual. Shanghai → Rotterdam keeps cooling, and the Istanbul desk took 9 hours on average to answer new rate requests.",
  lanes: [
    { lane: "INMAA → AEJEA", mode: "ocean", weeks: [4, 5, 5, 7, 9, 12], won: 5, quoted: 8 },
    { lane: "YYZ → IST", mode: "air", weeks: [2, 3, 2, 4, 3, 5], won: 2, quoted: 4 },
    { lane: "CNSHA → NLRTM", mode: "ocean", weeks: [10, 9, 9, 8, 7, 5], won: 1, quoted: 6 },
  ] satisfies DigestLane[],
  slowOffice: { office: "Istanbul", hours: 9, target: 4 },
};

/** An SVG polyline path through `values`, scaled into a `width` × `height` box (top = highest value). */
export function sparklinePath(values: readonly number[], width: number, height: number, padding = 2): string {
  if (values.length === 0) return "";
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const step = values.length > 1 ? (width - padding * 2) / (values.length - 1) : 0;
  return values
    .map((value, position) => {
      const x = padding + position * step;
      const y = max === min ? height / 2 : padding + (1 - (value - min) / span) * (height - padding * 2);
      return `${position === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

/** The latest week against the average of the weeks before it, in whole percent (0 when there is nothing to compare). */
export function weekTrend(values: readonly number[]): number {
  const last = values[values.length - 1];
  const earlier = values.slice(0, -1);
  if (last === undefined || earlier.length === 0) return 0;
  const average = earlier.reduce((sum, value) => sum + value, 0) / earlier.length;
  return average === 0 ? 0 : Math.round(((last - average) / average) * 100);
}

export const QUIET_DEAL_EXAMPLE = {
  company: "Northline Trading",
  contact: "Lena M.",
  lane: "Hamburg → Toronto",
  load: "1 × 40HC · quoted 1,980 USD",
  stage: "Quoted",
  quietAfterDays: 5,
  shownDays: 6,
  nextStep: "Ask whether the 12 Nov ready date still holds and offer to hold space on the next sailing.",
  draft:
    "Hi Lena, just checking in on the Hamburg → Toronto quote from last week. Is the 12 Nov ready date still on? If the volume has changed, I am happy to update the rate.",
};

/** A deal counts as quiet once `daysSinceActivity` reaches the threshold. */
export function isQuietDeal(daysSinceActivity: number, quietAfterDays: number): boolean {
  return quietAfterDays > 0 && daysSinceActivity >= quietAfterDays;
}

// --- journey of a reply ------------------------------------------------------------------------

/** Which of `count` stages a 0–1 scroll progress has reached (−1 before the first). */
export function journeyStage(progress: number, count: number): number {
  if (count <= 0 || progress <= 0) return -1;
  return Math.min(count - 1, Math.floor(Math.min(progress, 1) * count));
}

/** A small 3D tilt (degrees) for a pointer at (x, y) inside a `width` × `height` card. */
export function tiltFromPointer(x: number, y: number, width: number, height: number, maxDegrees = 6): { rotateX: number; rotateY: number } {
  if (width <= 0 || height <= 0) return { rotateX: 0, rotateY: 0 };
  const clamp = (value: number) => Math.max(-1, Math.min(1, value));
  const dx = clamp((x / width) * 2 - 1);
  const dy = clamp((y / height) * 2 - 1);
  // `+ 0` turns -0 into 0 so a centred pointer reads as "no tilt".
  const round = (value: number) => Math.round(value * 100) / 100 + 0;
  return { rotateX: round(-dy * maxDegrees), rotateY: round(dx * maxDegrees) };
}

// --- integrations and FAQ ----------------------------------------------------------------------

export interface Integration {
  name: string;
  kind: string;
  text: string;
  icon: "key" | "cloud" | "mail" | "bell" | "send" | "file" | "sparkles";
}

/** What LaneReach connects to today (names only, no logos). */
export const INTEGRATIONS: Integration[] = [
  { name: "Microsoft sign-in", kind: "Sign-in", icon: "key", text: "Single sign-on (OIDC) with your company's Microsoft identity, and roles for admins, pricing and reviewers." },
  { name: "SharePoint & OneDrive", kind: "Files", icon: "cloud", text: "Your script library and Excel rate sheets stay where they are; LaneReach keeps them in sync." },
  { name: "Any mailbox (SMTP + IMAP)", kind: "Email", icon: "mail", text: "Microsoft 365, Google Workspace with an app password, or your own server. Each agent sends from and reads their own mailbox." },
  { name: "Slack alerts", kind: "Alerts", icon: "bell", text: "Hot replies and alerts posted to the channels your team already watches." },
  { name: "Signed webhooks", kind: "Developers", icon: "send", text: "Events sent to your own systems with an HMAC signature, so you can check each one came from LaneReach." },
  { name: "Excel & CSV rate sheets", kind: "Rates", icon: "file", text: "Every tab of your rate workbooks read to price draft quotes; CSV works too." },
  { name: "Anthropic Claude or Azure OpenAI", kind: "AI", icon: "sparkles", text: "Pick the AI provider. Every AI run is logged with its prompt version and checked against a schema." },
];

export const FAQ: { question: string; answer: string }[] = [
  {
    question: "Does the AI send emails on its own?",
    answer:
      "Only if your reply policy allows it. By default, AI drafts wait for a person to review. Whether any email may go out at all is decided by the checks in the engine (send window, caps, suppression, kill switch), never by the AI.",
  },
  {
    question: "Do you scrape rate websites?",
    answer: "No. LaneReach never logs into other websites or copies their data. Draft quotes are priced from your own rate sheets and the rates your team records.",
  },
  {
    question: "Which languages does it work in?",
    answer:
      "The AI reads replies in any language. Outreach scripts are kept per language, so every office writes in words you approved; the AI can adapt a template to a new language for you to review.",
  },
  {
    question: "Where is my data, and who can see it?",
    answer:
      "Each company's data is kept apart by row-level security on every table in the database, and every request runs inside your company's scope. Sign-in is through your own Microsoft identity, with roles.",
  },
  {
    question: "Can I keep my Excel rates?",
    answer: "Yes. Keep your workbooks in SharePoint or OneDrive; LaneReach syncs them and reads every tab to price draft quotes. You can also upload Excel or CSV files.",
  },
  {
    question: "Which mailboxes can my agents use?",
    answer: "Any mailbox over SMTP and IMAP: Microsoft 365, Google Workspace with an app password, or your own mail server. Each agent sends from their own address and signature.",
  },
  {
    question: "Which AI does LaneReach use?",
    answer:
      "Anthropic Claude or Azure OpenAI, your choice. Email content is treated as data and never as instructions, every answer is checked against a schema, and each AI run is logged.",
  },
];

const AVAILABILITY_QUESTION = "Are lead scoring, the weekly digest and quiet-deal follow-ups available today?";

/** The FAQ, ending with where the new AI skills stand according to the running version. */
export function faqFor(liveTasks: readonly string[]): { question: string; answer: string }[] {
  const answer = upcomingAiLive(liveTasks)
    ? "Yes, they are part of the running version. The examples on this page use example data to show how they work."
    : "They are being built now. The examples on this page show how they will work; get in touch and we will tell you where they stand.";
  return [...FAQ, { question: AVAILABILITY_QUESTION, answer }];
}

/** Human labels for the AI tasks the running version has: known ones in reading order, new ones last. */
export function describeAiTasks(tasks: string[]): { id: string; title: string; detail: string }[] {
  const order = Object.keys(AI_TASK_LABELS);
  const rank = (id: string) => (order.includes(id) ? order.indexOf(id) : order.length);
  return [...tasks].sort((a, b) => rank(a) - rank(b)).map((id) => ({
    id,
    ...(AI_TASK_LABELS[id] ?? { title: id.replace(/_/g, " ").replace(/^\w/, (first) => first.toUpperCase()), detail: "" }),
  }));
}

export const AI_NEVER = [
  "Decide whether an email may be sent: the checks in the engine do (send window, caps, suppression, kill switch…).",
  "Invent a price: quotes come from your rate sheets and the rates your team records.",
  "Follow instructions hidden in an email: replies are treated as data, and every answer is checked against a schema.",
  "Send a quote or publish a workflow on its own: drafts wait for a person.",
];

// --- comparison -------------------------------------------------------------------------------

export type Support = "yes" | "partial" | "no";

export interface CompareRow {
  feature: string;
  hint: string;
  values: [Support, Support, Support, Support, Support];
}

/** LaneReach, then typical sales-outreach tools, general CRMs, freight software (TMS/rate tools), email + spreadsheets. */
export const COMPARE_COLUMNS = ["LaneReach", "Sales outreach tools", "General CRM", "Freight software", "Email + Excel"];

export const COMPARE_ROWS: CompareRow[] = [
  { feature: "Outreach per office, language and service line", hint: "Agents with their own mailbox, signature and scripts per country", values: ["yes", "partial", "partial", "no", "no"] },
  { feature: "AI that understands freight replies", hint: "Lanes, port codes, containers, Incoterms, ready dates", values: ["yes", "no", "no", "partial", "no"] },
  { feature: "Reply → deal → quote, automatically", hint: "A rate request opens a deal and a priced draft", values: ["yes", "no", "partial", "partial", "no"] },
  { feature: "Quotes from your own Excel rate sheets", hint: "Synced from SharePoint/OneDrive, every tab read", values: ["yes", "no", "no", "partial", "partial"] },
  { feature: "Freight charge lines per mode", hint: "Ocean, air, road, rail, courier, customs, warehousing, project", values: ["yes", "no", "partial", "yes", "partial"] },
  { feature: "Send rules enforced by the engine", hint: "Business days, send windows, caps per mailbox, company rotation, kill switch", values: ["yes", "partial", "partial", "no", "no"] },
  { feature: "Visual workflows with AI steps", hint: "Triggers, waits, branches, AI and protection steps on a canvas", values: ["yes", "partial", "partial", "no", "no"] },
  { feature: "Unified inbox across agents", hint: "Every agent mailbox, AI drafts, snooze, assign", values: ["yes", "partial", "partial", "no", "no"] },
  { feature: "Built-in assistant that answers from the docs", hint: "And says plainly what the product does not do", values: ["yes", "partial", "partial", "no", "no"] },
];

/** Rows where LaneReach is the only full "yes" (for the "only LaneReach" filter). */
export function onlyLaneReach(rows: CompareRow[]): CompareRow[] {
  return rows.filter((row) => row.values[0] === "yes" && row.values.slice(1).every((value) => value !== "yes"));
}

// --- contact ----------------------------------------------------------------------------------

export interface ContactForm {
  name: string;
  company: string;
  email: string;
  phone: string;
  country: string;
  message: string;
}

/** Field problems, or an empty object when the form can be sent. */
export function contactProblems(form: ContactForm): Partial<Record<keyof ContactForm, string>> {
  const problems: Partial<Record<keyof ContactForm, string>> = {};
  if (!form.name.trim()) problems.name = "Tell us your name";
  if (!/^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$/.test(form.email.trim())) problems.email = "Enter a valid work email";
  if (form.country.trim() && !/^[A-Za-z]{2}$/.test(form.country.trim())) problems.country = "Use the 2-letter code, e.g. AE";
  if (form.message.trim().length < 10) problems.message = "A few words about what you ship";
  if (form.message.length > 2000) problems.message = "At most 2,000 characters";
  return problems;
}

/** The JSON a LaneReach web form accepts (`website` is the honeypot and stays empty). */
export function contactPayload(form: ContactForm, honeypot = ""): Record<string, string> {
  const [first, ...rest] = form.name.trim().split(/\s+/);
  return {
    email: form.email.trim().toLowerCase(),
    first_name: first ?? "",
    last_name: rest.join(" "),
    company: form.company.trim(),
    phone: form.phone.trim(),
    country: form.country.trim().toUpperCase(),
    message: form.message.trim(),
    website: honeypot,
  };
}

// --- try the AI -------------------------------------------------------------------------------

export const BUCKET_LABELS: Record<string, { label: string; tone: "good" | "warn" | "stop" | "info" }> = {
  rate_query: { label: "Rate request", tone: "good" },
  interested: { label: "Interested", tone: "good" },
  meeting_request: { label: "Meeting request", tone: "good" },
  service_query: { label: "Service question", tone: "good" },
  partnership_query: { label: "Partnership enquiry", tone: "info" },
  referral: { label: "Referral", tone: "info" },
  wrong_office: { label: "Wrong office", tone: "info" },
  later: { label: "Ask again later", tone: "warn" },
  not_interested: { label: "Not interested", tone: "stop" },
  complaint: { label: "Complaint", tone: "stop" },
  existing_partner: { label: "Existing partner", tone: "info" },
  unsubscribe: { label: "Unsubscribe", tone: "stop" },
  out_of_office: { label: "Out of office", tone: "warn" },
  auto_reply: { label: "Automatic reply", tone: "info" },
  bounce_hard: { label: "Hard bounce", tone: "stop" },
  bounce_soft: { label: "Soft bounce", tone: "warn" },
  blocked: { label: "Blocked by the server", tone: "stop" },
  unclear: { label: "Unclear: a person decides", tone: "info" },
};

export interface TryExample {
  /** What the chip says: the kind of reply for English, the language's own name otherwise. */
  label: string;
  /** Language code shown on the chip (BCP 47 style, e.g. "PT-BR"). */
  code: string;
  /** The language in English, for screen readers. */
  language: string;
  subject: string;
  text: string;
}

/** Example replies for "try it yourself" (illustrative, believable freight replies in several languages). */
export const TRY_EXAMPLES: TryExample[] = [
  { label: "FCL quote", code: "EN", language: "English", subject: "Re: Freight to the Gulf", text: "Hi, please quote 2x40HC from Chennai to Jebel Ali, FOB, cargo ready 15 Oct. Commodity is ceramic tiles." },
  { label: "Air shipment", code: "EN", language: "English", subject: "Re: Air freight", text: "Can you give me a rate for 420 kg, 2.1 cbm, air from Toronto to Istanbul? Ready next Monday, DAP." },
  { label: "Español", code: "ES", language: "Spanish", subject: "Re: Envíos a México", text: "Hola, necesitamos cotización para 1x40HC de Shanghai a Manzanillo, mercancía lista el 20 de noviembre. Gracias." },
  {
    label: "Türkçe",
    code: "TR",
    language: "Turkish",
    subject: "Ynt: Körfez'e taşımacılık",
    text: "Merhaba, Ambarlı'dan Jebel Ali'ye 1x40HC için navlun teklifi rica ediyoruz. Yük 25 Kasım'da hazır, FOB. Ürün: ev tekstili.",
  },
  {
    label: "العربية",
    code: "AR",
    language: "Arabic",
    subject: "رد: الشحن إلى الخليج",
    text: "مرحباً، نرجو تزويدنا بعرض سعر لشحن حاويتين 40 قدم من شنغهاي إلى ميناء جبل علي. البضاعة جاهزة في 1 ديسمبر، والشروط FOB. شكراً.",
  },
  {
    label: "Deutsch",
    code: "DE",
    language: "German",
    subject: "AW: Transporte in den Nahen Osten",
    text: "Guten Tag, bitte senden Sie uns ein Angebot für eine Komplettladung (FTL, 24 t) von Hamburg nach Riad. Ladebereit am 2. Dezember, Incoterm DAP.",
  },
  {
    label: "Português",
    code: "PT-BR",
    language: "Portuguese (Brazil)",
    subject: "Re: Frete para a Europa",
    text: "Olá, poderiam cotar 1x20' de Santos para Valência? Carga pronta dia 28/11, café em grãos, FOB Santos. Obrigado!",
  },
  { label: "中文", code: "ZH", language: "Chinese", subject: "回复：欧洲海运", text: "您好，请报价从上海到鹿特丹的1x40HC海运费，货物12月3日备好，FOB上海。谢谢！" },
  {
    label: "हिन्दी",
    code: "HI",
    language: "Hindi",
    subject: "Re: खाड़ी के लिए शिपिंग",
    text: "नमस्ते, कृपया चेन्नई से जेबेल अली तक 2x40HC के लिए भाड़ा दर भेजें। माल 5 दिसंबर तक तैयार होगा, शर्तें FOB.",
  },
  {
    label: "Français",
    code: "FR",
    language: "French",
    subject: "Réponse automatique : Transport vers le Canada",
    text: "Bonjour, je suis absente du bureau jusqu'au 24 novembre. Pour toute demande urgente, merci de contacter notre service logistique.",
  },
  { label: "Out of office", code: "EN", language: "English", subject: "Automatic reply", text: "I am out of the office until 20 November with limited access to email. For urgent matters contact our operations team." },
  { label: "Unsubscribe", code: "EN", language: "English", subject: "Re: Freight rates", text: "Please remove me from your mailing list, we already have a forwarder." },
];

/** Shipment fields worth showing, in reading order, from the extraction output. */
export function shipmentRows(shipment: Record<string, unknown> | null | undefined): { label: string; value: string }[] {
  if (!shipment) return [];
  const place = (value: unknown) => {
    if (!value || typeof value !== "object") return "";
    const item = value as { city?: string | null; country?: string | null; port_code?: string | null };
    return [item.city, item.port_code ?? item.country].filter(Boolean).join(" · ");
  };
  const text = (value: unknown) => (value === null || value === undefined || value === "" || value === "UNKNOWN" || value === "unknown" ? "" : String(value));
  const containers = text(shipment.container_type) ? `${text(shipment.container_quantity) || "?"} × ${text(shipment.container_type)}` : "";
  const rows: [string, string][] = [
    ["Origin", place(shipment.origin)],
    ["Destination", place(shipment.destination)],
    ["Mode", text(shipment.mode)],
    ["Containers", containers],
    ["Weight", text(shipment.weight_kg) ? `${text(shipment.weight_kg)} kg` : ""],
    ["Volume", text(shipment.volume_cbm) ? `${text(shipment.volume_cbm)} cbm` : ""],
    ["Incoterm", text(shipment.incoterm)],
    ["Ready", text(shipment.cargo_ready_date)],
    ["Commodity", text(shipment.commodity)],
  ];
  return rows.filter(([, value]) => value).map(([label, value]) => ({ label, value }));
}

// --- time saved calculator --------------------------------------------------------------------

export interface SavingsInput {
  agents: number;
  repliesPerAgentPerDay: number;
  minutesPerReply: number;
  quotesPerWeek: number;
  minutesPerQuote: number;
  quoteShareSaved: number;
}

export const SAVINGS_DEFAULTS: SavingsInput = {
  agents: 10,
  repliesPerAgentPerDay: 12,
  minutesPerReply: 4,
  quotesPerWeek: 40,
  minutesPerQuote: 25,
  quoteShareSaved: 40,
};

const WORKDAYS_PER_MONTH = 21;
const WEEKS_PER_MONTH = 4.33;

/** Hours a month your assumptions add up to (reading and routing replies, preparing quotes). */
export function estimateSavings(input: SavingsInput): { replyHours: number; quoteHours: number; totalHours: number; fte: number } {
  const replyHours = (input.agents * input.repliesPerAgentPerDay * WORKDAYS_PER_MONTH * input.minutesPerReply) / 60;
  const quoteHours = (input.quotesPerWeek * WEEKS_PER_MONTH * input.minutesPerQuote * (input.quoteShareSaved / 100)) / 60;
  const totalHours = replyHours + quoteHours;
  return {
    replyHours: Math.round(replyHours),
    quoteHours: Math.round(quoteHours),
    totalHours: Math.round(totalHours),
    fte: Math.round((totalHours / (WORKDAYS_PER_MONTH * 8)) * 10) / 10,
  };
}
