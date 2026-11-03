/**
 * A tiny, safe Markdown renderer for help articles and assistant answers.
 *
 * Every character of the input is HTML-escaped first; only the tags this renderer writes can
 * appear in the output (headings, paragraphs, lists, tables, bold, italic, code, links). Links
 * are kept only for in-app paths and https URLs, so `javascript:` and similar never render.
 */

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function safeHref(url: string): string | null {
  const trimmed = url.trim();
  if (/^\/(?!\/)[\w\-./?=&#%]*$/.test(trimmed)) return trimmed;
  if (/^https:\/\/[\w.-]+(\/[\w\-./?=&#%~+]*)?$/i.test(trimmed)) return trimmed;
  return null;
}

/** Inline formatting on already-escaped text. */
function inline(text: string): string {
  let out = text;
  out = out.replace(/`([^`]+)`/g, "<code>$1</code>");
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/(^|[^*\w])\*([^*\s][^*]*)\*(?!\w)/g, "$1<em>$2</em>");
  out = out.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_match, label: string, url: string) => {
    const href = safeHref(url.replace(/&amp;/g, "&"));
    if (!href) return label;
    const external = href.startsWith("https://");
    return `<a href="${escapeHtml(href)}"${external ? ' target="_blank" rel="noreferrer"' : ""}>${label}</a>`;
  });
  return out;
}

function tableRow(line: string, cell: "th" | "td"): string {
  const cells = line.trim().replace(/^\||\|$/g, "").split("|");
  return `<tr>${cells.map((value) => `<${cell}>${inline(escapeHtml(value.trim()))}</${cell}>`).join("")}</tr>`;
}

/** Render Markdown to HTML that is safe to insert. */
export function renderMarkdown(source: string): string {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const html: string[] = [];
  let paragraph: string[] = [];
  let list: { tag: "ul" | "ol"; items: string[] } | null = null;

  const flushParagraph = () => {
    if (paragraph.length) html.push(`<p>${inline(escapeHtml(paragraph.join(" ")))}</p>`);
    paragraph = [];
  };
  const flushList = () => {
    if (list) html.push(`<${list.tag}>${list.items.map((item) => `<li>${inline(escapeHtml(item))}</li>`).join("")}</${list.tag}>`);
    list = null;
  };

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index] ?? "";
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
    const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    const isTable = line.trim().startsWith("|") && /^\s*\|?\s*:?-{3,}/.test(lines[index + 1] ?? "");
    if (isTable) {
      flushParagraph();
      flushList();
      const rows = [tableRow(line, "th")];
      index += 2;
      while (index < lines.length && (lines[index] ?? "").trim().startsWith("|")) {
        rows.push(tableRow(lines[index] ?? "", "td"));
        index += 1;
      }
      index -= 1;
      html.push(`<div class="md-table"><table>${rows[0]}${rows.slice(1).join("")}</table></div>`);
    } else if (heading) {
      flushParagraph();
      flushList();
      const level = Math.min(4, (heading[1] ?? "#").length + 1);
      html.push(`<h${level}>${inline(escapeHtml(heading[2] ?? ""))}</h${level}>`);
    } else if (bullet || numbered) {
      flushParagraph();
      const tag = bullet ? "ul" : "ol";
      if (!list || list.tag !== tag) {
        flushList();
        list = { tag, items: [] };
      }
      list.items.push((bullet ?? numbered)?.[1] ?? "");
    } else if (!line.trim()) {
      flushParagraph();
      flushList();
    } else if (list && /^\s{2,}\S/.test(line)) {
      list.items[list.items.length - 1] += ` ${line.trim()}`;
    } else {
      flushList();
      paragraph.push(line.trim());
    }
  }
  flushParagraph();
  flushList();
  return html.join("\n");
}
