/**
 * An email exactly as the recipient sees it, in a sandboxed iframe.
 *
 * The HTML is already sanitised by the backend; the frame adds a second wall: no scripts
 * (`sandbox` without `allow-scripts`), a CSP that only allows inline styles and images, and
 * links that cannot navigate the console. `allow-same-origin` is kept only so the frame's
 * height can be measured (without scripts the document cannot use that origin).
 */
import { useCallback, useRef, useState } from "react";

const CSP =
  "default-src 'none'; img-src https: http: data:; style-src 'unsafe-inline'; font-src https: data:";

export function buildEmailDocument(html: string, direction: "ltr" | "rtl" = "ltr"): string {
  return `<!doctype html><html dir="${direction}"><head><meta charset="utf-8">` +
    `<meta http-equiv="Content-Security-Policy" content="${CSP}">` +
    `<base target="_blank"><style>body{margin:0;padding:18px 20px;background:#fff;color:#1f2933;` +
    `font-family:Calibri,Arial,Helvetica,sans-serif;font-size:14px}img{max-width:100%;height:auto}</style>` +
    `</head><body>${html}</body></html>`;
}

/** Plain text shown as a (safe) HTML document. */
export function textToHtml(text: string): string {
  const escaped = text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
  return `<div style="white-space:pre-wrap">${escaped}</div>`;
}

export function EmailFrame({
  html,
  subject,
  from,
  to,
  direction,
  minHeight = 420,
}: {
  html: string;
  subject?: string;
  from?: string;
  to?: string;
  direction?: "ltr" | "rtl";
  minHeight?: number;
}) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(minHeight);
  const measure = useCallback(() => {
    const body = frame.current?.contentDocument?.body;
    if (body) setHeight(Math.max(minHeight, body.scrollHeight + 8));
  }, [minHeight]);
  return (
    <div className="email-frame">
      <div className="email-frame-bar">
        <i />
        <i />
        <i />
        <span style={{ marginLeft: 6 }}>Recipient's inbox preview</span>
      </div>
      {(subject || from || to) && (
        <div className="email-frame-meta">
          {subject && <div className="subject">{subject}</div>}
          {(from || to) && (
            <div style={{ color: "#56627a", fontSize: 12, marginTop: 2 }}>
              {from && <>From {from}</>}
              {from && to && " · "}
              {to && <>To {to}</>}
            </div>
          )}
        </div>
      )}
      <iframe
        ref={frame}
        title={subject ? `Email: ${subject}` : "Email preview"}
        sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"
        referrerPolicy="no-referrer"
        srcDoc={buildEmailDocument(html, direction)}
        style={{ height }}
        onLoad={measure}
      />
    </div>
  );
}
