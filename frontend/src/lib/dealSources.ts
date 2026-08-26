export const MARKETING_LEAD_SOURCES = [
  { value: "google_ads", label: "Google Ads" },
  { value: "meta_ads", label: "Meta Ads" },
  { value: "reddit_ads", label: "Reddit Ads" },
  { value: "chatgpt_ads", label: "ChatGPT Ads" },
  { value: "twitter_ads", label: "Twitter Ads" },
  { value: "bing_ads", label: "Bing Ads" },
  { value: "quora_ads", label: "Quora Ads" },
  { value: "g2_ads", label: "G2 Ads" },
  { value: "other", label: "Other" },
  { value: "events", label: "Events" },
] as const;

// Fixed list of events for the "Marketing lead → Events" picker, so reps
// select a known event instead of free-typing a name that drifts (typos,
// inconsistent formatting) across deals.
export const EVENT_OPTIONS = [
  "Bangalore Round Table",
  "TSIA WBN Live",
  "CS Summit, San Francisco",
  "CCO Summit, San Francisco",
  "Gainsight Pulse EU, London",
  "Ortus Club, London",
  "CS Summit, London",
  "CCO Summit, London",
] as const;

export const MARKETING_SOURCE_LABELS: Record<string, string> = {
  google_ads: "Google Ads",
  meta_ads: "Meta Ads",
  reddit_ads: "Reddit Ads",
  chatgpt_ads: "ChatGPT Ads",
  twitter_ads: "Twitter Ads",
  bing_ads: "Bing Ads",
  quora_ads: "Quora Ads",
  g2_ads: "G2 Ads",
  other: "Other",
  events: "Events",
};

export function parseMarketingSource(value?: string | null): { base: string; custom: string } {
  if (!value) return { base: "", custom: "" };
  const sep = value.indexOf(":");
  if (sep > 0) {
    const base = value.slice(0, sep);
    if (base === "other" || base === "events") return { base, custom: value.slice(sep + 1) };
  }
  return { base: value, custom: "" };
}

export function serializeMarketingSource(base: string, custom: string): string {
  const text = custom.trim();
  if (base === "other" || base === "events") return `${base}:${text}`;
  return base;
}
