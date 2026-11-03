/** Pure helpers for the pipeline board: columns, totals, days in stage, drops, money. */

export interface BoardDeal {
  id: string;
  stage_id: string;
  board_position: number;
  value_amount: number | null;
  currency: string;
  stage_entered_at: string;
  status: string;
}

export interface BoardStage {
  id: string;
  name: string;
  kind: "open" | "won" | "lost";
  probability: number;
}

export interface Column<T extends BoardDeal> {
  stage: BoardStage;
  deals: T[];
  count: number;
  value: number;
  weighted: number;
}

/** Deals grouped into the stage columns, in card order, with count and value per column. */
export function buildColumns<T extends BoardDeal>(stages: BoardStage[], deals: T[]): Column<T>[] {
  return stages.map((stage) => {
    const inStage = deals.filter((deal) => deal.stage_id === stage.id).sort((a, b) => a.board_position - b.board_position);
    const value = inStage.reduce((sum, deal) => sum + (deal.value_amount ?? 0), 0);
    return { stage, deals: inStage, count: inStage.length, value, weighted: (value * stage.probability) / 100 };
  });
}

/** Whole days a deal has been in its stage. */
export function daysInStage(enteredAt: string, now: number = Date.now()): number {
  const entered = new Date(enteredAt).getTime();
  if (Number.isNaN(entered)) return 0;
  return Math.max(0, Math.floor((now - entered) / 86_400_000));
}

/**
 * Where a card lands when dropped at `pointerY` in a column: the index of the first card whose
 * middle is below the pointer (cards are given as their top/height on screen). The card being
 * dragged is ignored, so dropping it back in place keeps its index.
 */
export function dropIndex(cards: { id: string; top: number; height: number }[], pointerY: number, draggedId: string): number {
  const others = cards.filter((card) => card.id !== draggedId);
  const index = others.findIndex((card) => pointerY < card.top + card.height / 2);
  return index === -1 ? others.length : index;
}

/** Move a card to `stageId` at `index` locally (optimistic update before the server answers). */
export function moveLocally<T extends BoardDeal>(deals: T[], dealId: string, stageId: string, index: number): T[] {
  const moving = deals.find((deal) => deal.id === dealId);
  if (!moving) return deals;
  const column = deals.filter((deal) => deal.stage_id === stageId && deal.id !== dealId).sort((a, b) => a.board_position - b.board_position);
  const before = column[index - 1]?.board_position;
  const after = column[index]?.board_position;
  const position =
    before === undefined && after === undefined
      ? 1024
      : before === undefined
        ? (after as number) - 1024
        : after === undefined
          ? before + 1024
          : (before + after) / 2;
  return deals.map((deal) => (deal.id === dealId ? { ...deal, stage_id: stageId, board_position: position } : deal));
}

/** "$4,200" or, for big numbers, "$125K" / "$1.3L" depending on the locale (cards, totals). */
export function formatMoney(value: number | null | undefined, currency = "USD", short = true, locale?: string): string {
  if (value === null || value === undefined) return "—";
  try {
    return new Intl.NumberFormat(locale, {
      style: "currency",
      currency,
      maximumFractionDigits: short && Math.abs(value) >= 10_000 ? 1 : 0,
      notation: short && Math.abs(value) >= 10_000 ? "compact" : "standard",
    }).format(value);
  } catch {
    return `${currency} ${Math.round(value)}`;
  }
}
