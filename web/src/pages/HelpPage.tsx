/** Help center: every article, searchable, rendered safely; "Ask about this" opens the assistant. */
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, unwrap } from "../api/client";
import { askAssistant } from "../components/Assistant";
import { Icon } from "../components/icons";
import { Page } from "../components/Layout";
import { SkeletonRows } from "../components/motion";
import { Button, Card, Empty, ErrorAlert, PageHeader } from "../components/ui";
import { renderMarkdown } from "../lib/markdown";

const CATEGORY_ORDER = ["Basics", "Outreach", "Automation", "AI", "CRM", "Delivery", "Tools"];

export function HelpPage() {
  const { slug } = useParams();
  return slug ? <ArticleView slug={slug} /> : <HelpIndex />;
}

function HelpIndex() {
  const [search, setSearch] = useState("");
  const articles = useQuery({ queryKey: ["help", "articles"], queryFn: () => unwrap(api.GET("/api/v1/help/articles")), staleTime: 300_000 });
  const groups = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const matching = (articles.data ?? []).filter((item) => !needle || `${item.title} ${item.summary}`.toLowerCase().includes(needle));
    const byCategory = new Map<string, typeof matching>();
    for (const item of matching) byCategory.set(item.category, [...(byCategory.get(item.category) ?? []), item]);
    return [...byCategory.entries()].sort((a, b) => CATEGORY_ORDER.indexOf(a[0]) - CATEGORY_ORDER.indexOf(b[0]));
  }, [articles.data, search]);
  return (
    <Page>
      <PageHeader
        eyebrow={<><Icon name="help" /> Help</>}
        title="Help center"
        description="How LaneReach works, screen by screen. The assistant answers from these articles."
        actions={
          <Button variant="primary" onClick={() => askAssistant()}>
            <Icon name="sparkles" /> Ask LaneReach
          </Button>
        }
      />
      <label className="search-field" style={{ maxWidth: 520, marginBottom: 16, display: "block" }}>
        <Icon name="search" />
        <input className="input" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search the help…" aria-label="Search the help" />
      </label>
      {articles.isLoading && <SkeletonRows rows={6} />}
      <ErrorAlert error={articles.error} />
      {!articles.isLoading && groups.length === 0 && (
        <Empty art="chart" title="Nothing matches">
          Try other words, or <button type="button" className="link-button" onClick={() => askAssistant(search)}>ask the assistant</button>.
        </Empty>
      )}
      <div className="help-groups">
        {groups.map(([category, items]) => (
          <section key={category}>
            <div className="nav-group-title">{category}</div>
            <div className="help-grid">
              {items.map((item) => (
                <Link key={item.slug} to={`/help/${item.slug}`} className="help-card plain">
                  <strong>{item.title}</strong>
                  <span className="small muted">{item.summary}</span>
                </Link>
              ))}
            </div>
          </section>
        ))}
      </div>
    </Page>
  );
}

function ArticleView({ slug }: { slug: string }) {
  const article = useQuery({
    queryKey: ["help", "article", slug],
    queryFn: () => unwrap(api.GET("/api/v1/help/articles/{slug}", { params: { path: { slug } } })),
    staleTime: 300_000,
  });
  const html = useMemo(() => (article.data ? renderMarkdown(article.data.body) : ""), [article.data]);
  return (
    <Page>
      <Link to="/help" className="btn ghost sm" style={{ marginBottom: 8 }}>
        ← Help center
      </Link>
      <ErrorAlert error={article.error} />
      {article.isLoading && <SkeletonRows rows={8} />}
      {article.data && (
        <>
          <PageHeader
            eyebrow={<><Icon name="help" /> {article.data.category}</>}
            title={article.data.title}
            description={article.data.summary}
            actions={
              <Button onClick={() => askAssistant(`About "${article.data?.title}": `, false)}>
                <Icon name="sparkles" /> Ask about this
              </Button>
            }
          />
          <Card>
            <article className="md help-article" dangerouslySetInnerHTML={{ __html: html }} />
          </Card>
        </>
      )}
    </Page>
  );
}
