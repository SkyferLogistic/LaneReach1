/** Money on the customer's quote page: `2,900.00 USD` (the same style as the quote email). */
export function formatQuoteMoney(amount: number, currency: string): string {
  return `${amount.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${currency}`;
}
