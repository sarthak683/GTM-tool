// ISO 4217 currency picker. Curated short list, not the full registry —
// covers every deal Beacon has closed so far and every market the team
// sells into. Add as needed; nothing in the backend rejects un-listed
// codes (we store any string up to 3 chars), but the picker hides what
// isn't here so a rep can't pick something nobody else will recognise.

export interface CurrencyOption {
  code: string;
  symbol: string;
  label: string;
  /** Group for the dropdown (popular vs. others). */
  group: "popular" | "other";
}

export const SUPPORTED_CURRENCY_CODES: ReadonlyArray<CurrencyOption> = [
  // Popular — the markets Beacon actively sells into.
  { code: "USD", symbol: "$", label: "US Dollar", group: "popular" },
  { code: "INR", symbol: "₹", label: "Indian Rupee", group: "popular" },
  { code: "EUR", symbol: "€", label: "Euro", group: "popular" },
  { code: "GBP", symbol: "£", label: "British Pound", group: "popular" },
  { code: "SGD", symbol: "S$", label: "Singapore Dollar", group: "popular" },
  { code: "AUD", symbol: "A$", label: "Australian Dollar", group: "popular" },
  // Long tail — keep behind a separator so the picker stays scannable.
  { code: "AED", symbol: "AED", label: "UAE Dirham", group: "other" },
  { code: "BRL", symbol: "R$", label: "Brazilian Real", group: "other" },
  { code: "CAD", symbol: "C$", label: "Canadian Dollar", group: "other" },
  { code: "CHF", symbol: "CHF", label: "Swiss Franc", group: "other" },
  { code: "CNY", symbol: "¥", label: "Chinese Yuan", group: "other" },
  { code: "HKD", symbol: "HK$", label: "Hong Kong Dollar", group: "other" },
  { code: "JPY", symbol: "¥", label: "Japanese Yen", group: "other" },
  { code: "KRW", symbol: "₩", label: "South Korean Won", group: "other" },
  { code: "MXN", symbol: "Mex$", label: "Mexican Peso", group: "other" },
  { code: "MYR", symbol: "RM", label: "Malaysian Ringgit", group: "other" },
  { code: "NZD", symbol: "NZ$", label: "New Zealand Dollar", group: "other" },
  { code: "PHP", symbol: "₱", label: "Philippine Peso", group: "other" },
  { code: "SEK", symbol: "kr", label: "Swedish Krona", group: "other" },
  { code: "ZAR", symbol: "R", label: "South African Rand", group: "other" },
];

const BY_CODE: Record<string, CurrencyOption> = Object.fromEntries(
  SUPPORTED_CURRENCY_CODES.map((c) => [c.code, c]),
);

export function getCurrencyOption(code?: string | null): CurrencyOption {
  if (!code) return BY_CODE.USD;
  return BY_CODE[code.toUpperCase()] ?? {
    code: code.toUpperCase(),
    symbol: code.toUpperCase(),
    label: code.toUpperCase(),
    group: "other",
  };
}

export function formatCurrencyAmount(
  value?: number | null,
  currencyCode?: string | null,
): string {
  if (value == null) return "—";
  const opt = getCurrencyOption(currencyCode);
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: opt.code,
      minimumFractionDigits: 0,
      maximumFractionDigits: 0,
    }).format(value);
  } catch {
    // Unknown ISO code — fall back to the currency symbol as a prefix.
    return `${opt.symbol}${value.toLocaleString()}`;
  }
}
