/** Fuzzy matching for the command palette. */

export interface Searchable {
  label: string;
  keywords?: string[];
}

/**
 * Score how well `query` matches `text` (higher is better, null = no match).
 *
 * Every query character must appear in order. Matches at the start of the text or of a word,
 * and consecutive characters, score higher, so "scr" ranks "Scripts" above "Subscriptions".
 */
export function fuzzyScore(query: string, text: string): number | null {
  const needle = query.trim().toLowerCase();
  if (!needle) return 0;
  const haystack = text.toLowerCase();
  let score = 0;
  let position = -1;
  let streak = 0;
  for (const character of needle) {
    if (character === " ") continue;
    const found = haystack.indexOf(character, position + 1);
    if (found === -1) return null;
    const wordStart = found === 0 || /[\s\-_/(]/.test(haystack.charAt(found - 1));
    streak = found === position + 1 ? streak + 1 : 0;
    score += 1 + (wordStart ? 4 : 0) + streak * 2 - Math.min(found - position - 1, 5) * 0.2;
    position = found;
  }
  return score - haystack.length * 0.01;
}

/** Items matching `query`, best first (all items, in order, when the query is empty). */
export function rankItems<T extends Searchable>(query: string, items: T[]): T[] {
  if (!query.trim()) return items;
  return items
    .map((item) => {
      const scores = [item.label, ...(item.keywords ?? [])]
        .map((text) => fuzzyScore(query, text))
        .filter((value): value is number => value !== null);
      return { item, score: scores.length ? Math.max(...scores) : null };
    })
    .filter((entry): entry is { item: T; score: number } => entry.score !== null)
    .sort((a, b) => b.score - a.score)
    .map((entry) => entry.item);
}
