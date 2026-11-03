/** Quote maths for the builder: the same rounding as the server (half-up to cents per line). */

export interface DraftLine {
  charge_type: string;
  description: string;
  quantity: string;
  unit: string;
  unit_price: string;
}

function cents(value: number): number {
  // Half-up on the cent, avoiding binary drift (1.005 → 1.01).
  return Math.round((value + Number.EPSILON) * 100) / 100;
}

/** The unit price as the server stores it: whole cents ("185.555" → "185.56"). */
export function normalizePrice(value: string): string {
  const price = Number(value);
  return Number.isFinite(price) ? cents(price).toFixed(2) : "0.00";
}

/** The quantity as the server stores it: at most three decimals. */
export function normalizeQuantity(value: string): string {
  const quantity = Number(value);
  return Number.isFinite(quantity) ? String(Math.round((quantity + Number.EPSILON) * 1000) / 1000) : "0";
}

/** Quantity × unit price (both as stored), rounded like an invoice line. */
export function lineAmount(line: Pick<DraftLine, "quantity" | "unit_price">): number {
  const quantity = Number(normalizeQuantity(line.quantity));
  const price = Number(normalizePrice(line.unit_price));
  if (!Number.isFinite(Number(line.quantity)) || !Number.isFinite(Number(line.unit_price))) return 0;
  return cents(quantity * price);
}

/** Sum of the rounded line amounts. */
export function quoteTotal(lines: Pick<DraftLine, "quantity" | "unit_price">[]): number {
  return cents(lines.reduce((sum, line) => sum + lineAmount(line), 0));
}

/** A line is sendable with a description, a positive quantity and a price. */
export function lineIsValid(line: DraftLine): boolean {
  return line.description.trim().length > 0 && Number(line.quantity) > 0 && line.unit_price.trim() !== "" && Number(line.unit_price) >= 0;
}

/** Days left until a validity date (negative when past). */
export function daysLeft(validUntil: string, now: Date = new Date()): number {
  const [year, month, day] = validUntil.split("-").map(Number);
  if (!year || !month || !day) return 0;
  const end = Date.UTC(year, month - 1, day);
  const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.round((end - today) / 86_400_000);
}
