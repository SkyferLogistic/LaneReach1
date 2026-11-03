/**
 * Contact us: posts to the operator's LaneReach web form (the enquiry becomes a contact in its
 * CRM, so workflows and Slack alerts can pick it up). Falls back to email when no form is set.
 */
import { useState } from "react";
import { Link } from "react-router-dom";
import { fireConfetti } from "../../components/delight";
import { Icon } from "../../components/icons";
import { contactPayload, contactProblems } from "../../lib/landing";
import type { ContactForm } from "../../lib/landing";

const EMPTY: ContactForm = { name: "", company: "", email: "", phone: "", country: "", message: "" };

export function Contact({ formKey, email, signedIn }: { formKey: string | null; email: string | null; signedIn: boolean }) {
  const [form, setForm] = useState<ContactForm>(EMPTY);
  const [honeypot, setHoneypot] = useState("");
  const [touched, setTouched] = useState(false);
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [error, setError] = useState("");
  const problems = contactProblems(form);
  const show = (field: keyof ContactForm) => (touched ? problems[field] : undefined);
  const set = (field: keyof ContactForm) => (event: { target: { value: string } }) => setForm((current) => ({ ...current, [field]: event.target.value }));

  const submit = async (target: HTMLElement) => {
    setTouched(true);
    if (Object.keys(problems).length || !formKey) return;
    setState("sending");
    try {
      const response = await fetch(`/api/v1/public/forms/${encodeURIComponent(formKey)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(contactPayload(form, honeypot)),
      });
      if (!response.ok) {
        const body = (await response.json().catch(() => ({}))) as { detail?: string };
        throw new Error(body.detail ?? "Something went wrong. Please try again.");
      }
      setState("sent");
      const box = target.getBoundingClientRect();
      fireConfetti({ x: box.left + box.width / 2, y: box.top });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
      setState("error");
    }
  };

  return (
    <div className="contact-grid">
      <div className="contact-card">
        {state === "sent" ? (
          <div className="contact-sent">
            <span className="contact-check">
              <Icon name="check" />
            </span>
            <h3>Thank you, {form.name.split(" ")[0] || "we got it"}.</h3>
            <p>Your message reached our team; we will get back to you by email.</p>
            <button
              type="button"
              className="site-btn ghost"
              onClick={() => {
                setForm(EMPTY);
                setTouched(false);
                setState("idle");
              }}
            >
              Send another message
            </button>
          </div>
        ) : formKey ? (
          <form
            noValidate
            onSubmit={(event) => {
              event.preventDefault();
              void submit(event.currentTarget);
            }}
          >
            <div className="contact-fields">
              <Field label="Your name" error={show("name")}>
                <input value={form.name} onChange={set("name")} autoComplete="name" maxLength={200} />
              </Field>
              <Field label="Company">
                <input value={form.company} onChange={set("company")} autoComplete="organization" maxLength={200} />
              </Field>
              <Field label="Work email" error={show("email")}>
                <input type="email" value={form.email} onChange={set("email")} autoComplete="email" maxLength={320} />
              </Field>
              <Field label="Phone (optional)">
                <input type="tel" value={form.phone} onChange={set("phone")} autoComplete="tel" maxLength={40} />
              </Field>
              <Field label="Country (2 letters)" error={show("country")}>
                <input value={form.country} onChange={set("country")} maxLength={2} placeholder="AE" />
              </Field>
              <Field label="What do you ship, and where?" error={show("message")} wide>
                <textarea value={form.message} onChange={set("message")} rows={4} maxLength={2000} placeholder="e.g. FCL from India to the Gulf, 6 offices, 20 sales agents" />
              </Field>
              {/* Bots fill every field; people never see this one. */}
              <input className="contact-trap" tabIndex={-1} autoComplete="off" aria-hidden value={honeypot} onChange={(event) => setHoneypot(event.target.value)} name="website" />
            </div>
            {state === "error" && <div className="contact-error">{error}</div>}
            <button type="submit" className="site-btn primary wide" disabled={state === "sending"}>
              {state === "sending" ? "Sending…" : "Talk to the LaneReach team"} <Icon name="send" />
            </button>
            <p className="contact-fine">We use your details only to answer you.</p>
          </form>
        ) : (
          <div className="contact-sent">
            <span className="contact-check muted">
              <Icon name="mail" />
            </span>
            <h3>Write to us</h3>
            {email ? (
              <a className="site-btn primary" href={`mailto:${email}?subject=LaneReach`}>
                {email}
              </a>
            ) : (
              <p>The contact form is not switched on yet. Set PUBLIC_CONTACT_FORM_KEY to a web form of your workspace.</p>
            )}
          </div>
        )}
      </div>
      <aside className="contact-side">
        <div className="contact-next">
          <h3>What happens next</h3>
          <ol>
            <li>
              <b>1</b> We get back to you by email.
            </li>
            <li>
              <b>2</b> A walkthrough on your own lanes.
            </li>
            <li>
              <b>3</b> A workspace with your offices, scripts and rate sheets.
            </li>
          </ol>
        </div>
        <div className="contact-signin">
          <Icon name="key" />
          <div>
            <strong>{signedIn ? "You are signed in" : "Already using LaneReach?"}</strong>
            <span>{signedIn ? "Go back to your workspace." : "Sign in with your company account."}</span>
          </div>
          <Link to={signedIn ? "/" : "/login"} className="site-btn ghost">
            {signedIn ? "Open LaneReach" : "Sign in"}
          </Link>
        </div>
      </aside>
    </div>
  );
}

function Field({ label, error, wide, children }: { label: string; error?: string; wide?: boolean; children: React.ReactNode }) {
  return (
    <label className={["contact-field", wide ? "wide" : "", error ? "bad" : ""].filter(Boolean).join(" ")}>
      <span>{label}</span>
      {children}
      {error && <small>{error}</small>}
    </label>
  );
}
