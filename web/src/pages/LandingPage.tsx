/**
 * The public website at `/` (and `/welcome`): what LaneReach does, the AI at work, the AI co-pilot
 * features being built, the AI skills orbit, the journey of a reply, how it compares, integrations,
 * FAQ, the numbers of the running version (`GET /site`), sign in and contact us. No sign-in needed.
 */
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent, ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { api, unwrap } from "../api/client";
import { useSession } from "../auth/session";
import { ThemeSwitch } from "../components/ThemeSwitch";
import { Reveal, useSpotlight } from "../components/delight";
import { Icon } from "../components/icons";
import { BrandMark } from "../components/Layout";
import { CountUp, useReducedMotion } from "../components/motion";
import { TICKER, upcomingAiLive } from "../lib/landing";
import { usePublicTheme } from "../lib/theme";
import { CoPilot } from "./landing/CoPilot";
import { Contact } from "./landing/Contact";
import { Faq } from "./landing/Faq";
import { InboxDemo } from "./landing/InboxDemo";
import { Integrations } from "./landing/Integrations";
import { Journey } from "./landing/Journey";
import { Savings } from "./landing/Savings";
import { AiSection, Bento, Compare, HowItWorks } from "./landing/Sections";
import { TryAi } from "./landing/TryAi";
import { WorldLanes } from "./landing/WorldLanes";
import "./landing/landing.css";

const WORDS = ["ocean", "air", "road", "rail", "customs", "project"];
const NAV = [
  ["product", "Product"],
  ["ai", "AI"],
  ["copilot", "Co-pilot"],
  ["how", "How it works"],
  ["compare", "Compare"],
  ["faq", "FAQ"],
  ["contact", "Contact"],
] as const;
const FOOTER_NAV = [...NAV.slice(0, 5), ["integrations", "Integrations"], ...NAV.slice(5)] as const;

export function LandingPage() {
  const { session } = useSession();
  const facts = useQuery({ queryKey: ["site"], queryFn: () => unwrap(api.GET("/api/v1/site")), staleTime: 300_000 });
  const reduced = useReducedMotion();
  const theme = usePublicTheme();
  const [word, setWord] = useState(0);
  const [scrolled, setScrolled] = useState(false);
  const [menu, setMenu] = useState(false);
  const location = useLocation();
  const hero = useRef<HTMLElement>(null);
  useSpotlight();

  // `/welcome#contact` (from the sign-in page) opens at that section.
  useEffect(() => {
    const id = location.hash.slice(1);
    if (!id) return undefined;
    const timer = window.setTimeout(() => document.getElementById(id)?.scrollIntoView({ block: "start" }), 250);
    return () => window.clearTimeout(timer);
  }, [location.hash]);

  useEffect(() => {
    document.title = "LaneReach · AI outreach and CRM for freight forwarders";
    const onScroll = () => setScrolled(window.scrollY > 24);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      document.title = "LaneReach";
    };
  }, []);

  useEffect(() => {
    if (reduced) return undefined;
    const timer = window.setInterval(() => setWord((current) => (current + 1) % WORDS.length), 2200);
    return () => window.clearInterval(timer);
  }, [reduced]);

  const data = facts.data;
  const checks = data?.send_checks.length;
  const steps = data?.workflow_steps.total;
  const lines = data?.service_lines.length;
  const tasks = data?.ai_tasks ?? [];
  const newAiLive = upcomingAiLive(tasks);
  const go = (id: string) => {
    setMenu(false);
    document.getElementById(id)?.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" });
  };
  // A soft light follows a mouse pointer over the hero (CSS reads --hx/--hy; no re-render per move).
  const glow = (event: ReactPointerEvent<HTMLElement>) => {
    if (reduced || event.pointerType !== "mouse" || !hero.current) return;
    const box = hero.current.getBoundingClientRect();
    hero.current.style.setProperty("--hx", `${event.clientX - box.left}px`);
    hero.current.style.setProperty("--hy", `${event.clientY - box.top}px`);
    hero.current.classList.add("glowing");
  };
  const unglow = () => hero.current?.classList.remove("glowing");

  return (
    <div className={theme.light ? "site light" : "site"}>
      <header className={scrolled ? "site-nav solid" : "site-nav"}>
        <a className="site-brand" href="#top" onClick={(event) => (event.preventDefault(), go("top"))}>
          <BrandMark /> LaneReach
        </a>
        <nav className={menu ? "site-links open" : "site-links"} aria-label="Sections">
          {NAV.map(([id, label]) => (
            <button key={id} type="button" onClick={() => go(id)}>
              {label}
            </button>
          ))}
        </nav>
        <div className="site-nav-actions">
          <ThemeSwitch light={theme.light} onToggle={theme.toggle} />
          <Link to={session ? "/" : "/login"} className="site-btn ghost sm">
            {session ? "Open LaneReach" : "Sign in"}
          </Link>
          <button type="button" className="site-btn primary sm hide-mobile" onClick={() => go("contact")}>
            Contact us
          </button>
          <button type="button" className="site-menu" aria-label="Menu" aria-expanded={menu} onClick={() => setMenu(!menu)}>
            <Icon name={menu ? "x" : "menu"} />
          </button>
        </div>
      </header>

      <section className="site-hero" id="top" ref={hero} onPointerMove={glow} onPointerLeave={unglow}>
        <div className="hero-aurora" aria-hidden>
          <span />
          <span />
          <span />
        </div>
        <div className="hero-glow" aria-hidden />
        <div className="hero-copy">
          <span className="site-pill">
            <Icon name="sparkles" /> AI outreach &amp; CRM built for freight forwarders
          </span>
          <h1>
            Win more{" "}
            <span className="hero-word" key={word}>
              {WORDS[word]}
            </span>
            <br />
            freight, on autopilot.
          </h1>
          <p>
            LaneReach writes to importers and exporters from every office in their own language, reads every reply with AI, opens the deal with the shipment filled in and
            drafts the quote from your own rate sheets. Your team only does the part that needs a person.
          </p>
          <div className="hero-ctas">
            <Magnetic>
              <button type="button" className="site-btn primary lg" onClick={() => go("contact")}>
                Talk to us <Icon name="send" />
              </button>
            </Magnetic>
            <button type="button" className="site-btn ghost lg" onClick={() => go(data?.ai_demo ? "try" : "ai")}>
              <Icon name={data?.ai_demo ? "sparkles" : "play"} /> {data?.ai_demo ? "Try the AI on your email" : "Watch the AI work"}
            </button>
          </div>
          <ul className="hero-facts">
            <Fact value={lines} label="service lines" />
            <Fact value={steps} label="workflow steps" />
            <Fact value={checks} label="checks on every email" />
            <Fact value={data?.ai_tasks.length} label="AI skills" />
          </ul>
        </div>
        <div className="hero-visual">
          <WorldLanes />
        </div>
      </section>

      <div className="site-ticker" aria-hidden>
        <div className="ticker-track">
          {[...TICKER, ...TICKER].map((lane, position) => (
            <span key={position}>
              <Icon name={position % 3 === 0 ? "plane" : position % 3 === 1 ? "ship" : "truck"} /> {lane}
            </span>
          ))}
        </div>
      </div>

      <Section id="ai" eyebrow="The AI at work" title="Every reply read, sorted and acted on in seconds" lead="Pick an example reply and watch what LaneReach does with it.">
        <InboxDemo />
      </Section>

      {data?.ai_demo && (
        <Section id="try" eyebrow="Try it yourself" title="Paste a real reply. Watch LaneReach read it." lead="The same reply AI that runs in LaneReach: it sorts the reply, pulls out the shipment and tells you what would happen next, in any language.">
          <TryAi />
        </Section>
      )}

      <Section id="product" eyebrow="Product" title="Everything a forwarder's sales desk needs, in one place" lead="Outreach, inbox, CRM, pricing and automation share one engine, so nothing is copied between tools.">
        <Bento checks={checks} steps={steps} lines={lines} />
      </Section>

      <Section
        id="copilot"
        eyebrow={newAiLive ? "AI co-pilot" : "AI co-pilot · coming soon"}
        title="Three new AI helpers for the sales desk"
        lead={`${newAiLive ? "New" : "Being built now"}: a score for every lead, a weekly digest written from your numbers, and follow-ups for deals that went quiet. Shown here with example data.`}
      >
        <CoPilot tasks={tasks} />
      </Section>

      <Section
        id="ai-scope"
        eyebrow="AI command center"
        title="Every AI skill in one place, and clear limits"
        lead="Point at a skill to see what it does. What the AI may never do is decided by the engine, not by a prompt."
      >
        <AiSection tasks={tasks} />
      </Section>

      <Section id="how" eyebrow="How it works" title="From first email to quote, in four steps">
        <HowItWorks />
      </Section>

      <Section id="journey" eyebrow="Journey of a reply" title="Follow one reply from inbox to booked" lead="Scroll along the line. Each step says who does the work: the engine, the AI or a person.">
        <Journey newAiLive={newAiLive} />
      </Section>

      <Section id="compare" eyebrow="Compare" title="Why forwarders choose LaneReach over stitching tools together" lead="General tools can send emails or store deals. LaneReach knows what a 40HC from Chennai to Jebel Ali is.">
        <Compare />
      </Section>

      <Section id="integrations" eyebrow="Integrations" title="Works with the tools your desk already uses" lead="Sign in with Microsoft, keep your files in SharePoint, send from your own mailboxes. No logos, just what really connects today.">
        <Integrations onContact={() => go("contact")} />
      </Section>

      <Section id="savings" eyebrow="Your numbers" title="How much time would your desk get back?" lead="Move the sliders to match your team. The maths is shown, nothing is hidden.">
        <Savings />
      </Section>

      <Section id="faq" eyebrow="FAQ" title="Questions forwarders ask us">
        <Faq tasks={tasks} onContact={() => go("contact")} />
      </Section>

      <section className="site-band">
        <Reveal>
          <div className="band-grid">
            <BandItem icon="shield" title="Your data stays yours" text="Each company's data is kept apart by row-level security in the database, on every table." />
            <BandItem icon="key" title="Your sign-in" text="Microsoft sign-in with your company's identity provider and roles for admins, pricing and reviewers." />
            <BandItem icon="file" title="Everything audited" text="Send decisions, AI runs and admin changes are recorded, with who and why." />
            <BandItem icon="external" title="No scraping, no shortcuts" text="LaneReach never logs into other websites or copies their data." />
          </div>
        </Reveal>
      </section>

      <Section id="contact" eyebrow="Contact" title="See LaneReach on your own lanes" lead="Tell us about your offices and what you ship. We will show you LaneReach with your scripts and rates.">
        <Contact formKey={data?.contact.form_key ?? null} email={data?.contact.email ?? null} signedIn={Boolean(session)} />
      </Section>

      <footer className="site-footer">
        <div className="site-brand">
          <BrandMark /> LaneReach
        </div>
        <span>AI outreach, CRM and pricing for freight forwarders.</span>
        <nav>
          {FOOTER_NAV.map(([id, label]) => (
            <button key={id} type="button" onClick={() => go(id)}>
              {label}
            </button>
          ))}
          <Link to={session ? "/" : "/login"}>{session ? "Open LaneReach" : "Sign in"}</Link>
        </nav>
      </footer>
    </div>
  );
}

function Section({ id, eyebrow, title, lead, children }: { id: string; eyebrow: string; title: string; lead?: string; children: ReactNode }) {
  return (
    <section className="site-section" id={id}>
      <Reveal>
        <header className="section-head">
          <span className="section-eyebrow">{eyebrow}</span>
          <h2>{title}</h2>
          {lead && <p>{lead}</p>}
        </header>
      </Reveal>
      <Reveal delay={120}>{children}</Reveal>
    </section>
  );
}

function Fact({ value, label }: { value: number | undefined; label: string }) {
  return (
    <li>
      <strong>{value === undefined ? "–" : <CountUp value={value} />}</strong>
      <span>{label}</span>
    </li>
  );
}

function BandItem({ icon, title, text }: { icon: Parameters<typeof Icon>[0]["name"]; title: string; text: string }) {
  return (
    <div className="band-item">
      <Icon name={icon} />
      <strong>{title}</strong>
      <span>{text}</span>
    </div>
  );
}

/** A button that leans towards the cursor. */
function Magnetic({ children }: { children: ReactNode }) {
  const element = useRef<HTMLSpanElement>(null);
  const reduced = useReducedMotion();
  const move = (event: ReactPointerEvent<HTMLSpanElement>) => {
    if (reduced || !element.current) return;
    const box = element.current.getBoundingClientRect();
    const x = (event.clientX - box.left - box.width / 2) * 0.25;
    const y = (event.clientY - box.top - box.height / 2) * 0.35;
    element.current.style.transform = `translate(${x}px, ${y}px)`;
  };
  const leave = () => element.current && (element.current.style.transform = "");
  return (
    <span ref={element} className="magnetic" onPointerMove={move} onPointerLeave={leave}>
      {children}
    </span>
  );
}
