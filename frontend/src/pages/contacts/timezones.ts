/**
 * One definition of how this app understands a contact's timezone.
 *
 * These lived in Contacts.tsx while CallDispositionDrawer.tsx kept its own
 * copies, and the copies had drifted apart in a way reps could see:
 *
 *   canonicalTimezone   the drawer's copy read `value.includes("/") ? value : value`
 *                       — both branches identical, so a short label was never
 *                       resolved to a real IANA zone. It then reached
 *                       toLocaleTimeString({timeZone}), which throws on an
 *                       invalid zone, and a try/catch hid it as "no local time".
 *   formatTimezoneLabel the drawer stripped the path and upper-cased it, so all
 *                       4,474 prod contacts with a timezone showed a different
 *                       chip depending on which page the drawer was opened from:
 *                       NEW YORK vs ET, KOLKATA vs IST, LONDON vs GMT.
 *
 * Importing from here is what stops that happening again.
 */
export const TIMEZONE_GROUPS = [
  { value: "America/New_York", code: "ET", label: "Eastern US / Canada", aliases: ["EST"], zones: ["America/New_York", "America/Toronto"] },
  { value: "America/Chicago", code: "CT", label: "Central US / Canada / Mexico", aliases: ["CST"], zones: ["America/Chicago", "America/Winnipeg", "America/Mexico_City", "America/Costa_Rica", "America/Managua", "America/El_Salvador"] },
  { value: "America/Denver", code: "MT", label: "Mountain US / Canada", aliases: ["MST"], zones: ["America/Denver", "America/Phoenix", "America/Edmonton"] },
  { value: "America/Los_Angeles", code: "PT", label: "Pacific US / Canada", aliases: ["PST"], zones: ["America/Los_Angeles", "America/Vancouver"] },
  { value: "America/Halifax", code: "AT", label: "Atlantic Canada", aliases: ["AST-CA"], zones: ["America/Halifax"] },
  { value: "America/Anchorage", code: "AKT", label: "Alaska", aliases: ["AKST"], zones: ["America/Anchorage"] },
  { value: "Pacific/Honolulu", code: "HT", label: "Hawaii", aliases: ["HST"], zones: ["Pacific/Honolulu"] },
  { value: "America/Sao_Paulo", code: "BRT", label: "Brazil", aliases: [], zones: ["America/Sao_Paulo"] },
  { value: "America/Argentina/Buenos_Aires", code: "ART", label: "Argentina", aliases: [], zones: ["America/Argentina/Buenos_Aires"] },
  { value: "America/Bogota", code: "COT", label: "Colombia", aliases: [], zones: ["America/Bogota"] },
  { value: "Europe/London", code: "UK", label: "UK / Ireland / Portugal", aliases: ["GMT"], zones: ["Europe/London", "Europe/Dublin", "Europe/Lisbon"] },
  { value: "Atlantic/Reykjavik", code: "GMT", label: "Iceland", aliases: [], zones: ["Atlantic/Reykjavik"] },
  { value: "Europe/Berlin", code: "CET", label: "Central Europe", aliases: ["CET"], zones: ["Europe/Berlin", "Europe/Paris", "Europe/Amsterdam", "Europe/Madrid", "Europe/Rome", "Europe/Budapest", "Europe/Belgrade", "Europe/Zagreb", "Europe/Ljubljana", "Europe/Stockholm", "Europe/Warsaw", "Europe/Oslo", "Europe/Brussels", "Europe/Copenhagen", "Europe/Zurich", "Europe/Prague", "Europe/Vienna", "Africa/Tunis"] },
  { value: "Europe/Athens", code: "EET", label: "Eastern Europe", aliases: ["EET"], zones: ["Europe/Athens", "Europe/Bucharest", "Europe/Sofia", "Europe/Helsinki", "Europe/Vilnius", "Europe/Riga", "Asia/Beirut", "Africa/Cairo"] },
  { value: "Europe/Moscow", code: "MSK", label: "Moscow", aliases: [], zones: ["Europe/Moscow"] },
  { value: "Europe/Istanbul", code: "TRT", label: "Turkey", aliases: [], zones: ["Europe/Istanbul"] },
  { value: "Africa/Johannesburg", code: "SAST", label: "South Africa", aliases: [], zones: ["Africa/Johannesburg"] },
  { value: "Africa/Lagos", code: "WAT", label: "West Africa", aliases: [], zones: ["Africa/Lagos"] },
  { value: "Asia/Kolkata", code: "IST", label: "India / Sri Lanka", aliases: ["IST"], zones: ["Asia/Kolkata", "Asia/Calcutta", "Asia/Colombo"] },
  { value: "Asia/Jerusalem", code: "IL", label: "Israel", aliases: ["IDT"], zones: ["Asia/Jerusalem"] },
  { value: "Asia/Dubai", code: "GST", label: "UAE / Oman", aliases: ["GST"], zones: ["Asia/Dubai", "Asia/Muscat"] },
  { value: "Asia/Riyadh", code: "KSA", label: "Saudi Arabia", aliases: ["AST-SA"], zones: ["Asia/Riyadh"] },
  { value: "Asia/Tehran", code: "IRST", label: "Iran", aliases: [], zones: ["Asia/Tehran"] },
  { value: "Asia/Karachi", code: "PKT", label: "Pakistan", aliases: [], zones: ["Asia/Karachi"] },
  { value: "Asia/Bangkok", code: "ICT", label: "Thailand", aliases: [], zones: ["Asia/Bangkok"] },
  { value: "Asia/Jakarta", code: "WIB", label: "Western Indonesia", aliases: [], zones: ["Asia/Jakarta"] },
  { value: "Asia/Singapore", code: "UTC+8", label: "Singapore / China / Hong Kong / Malaysia / Philippines", aliases: ["SGT"], zones: ["Asia/Singapore", "Asia/Shanghai", "Asia/Hong_Kong", "Asia/Kuala_Lumpur", "Asia/Kuching", "Asia/Manila"] },
  { value: "Asia/Seoul", code: "KST", label: "South Korea", aliases: [], zones: ["Asia/Seoul"] },
  { value: "Asia/Tokyo", code: "JST", label: "Japan", aliases: ["JST"], zones: ["Asia/Tokyo"] },
  { value: "Indian/Mauritius", code: "MUT", label: "Mauritius", aliases: [], zones: ["Indian/Mauritius"] },
  { value: "Australia/Sydney", code: "AU East", label: "Eastern Australia", aliases: ["AEST"], zones: ["Australia/Sydney", "Australia/Melbourne", "Australia/Brisbane"] },
  { value: "Pacific/Auckland", code: "NZ", label: "New Zealand", aliases: ["NZST"], zones: ["Pacific/Auckland"] },
] as const;

export const TIMEZONE_OPTIONS = TIMEZONE_GROUPS.map(({ value, code, label }) => ({
  value,
  label: `${code} - ${label}`,
}));

export function timezoneGroup(value?: string | null) {
  if (!value) return undefined;
  return TIMEZONE_GROUPS.find((group) =>
    group.value === value ||
    group.zones.some((zone) => zone === value) ||
    group.aliases.some((alias) => alias === value)
  );
}

export function canonicalTimezone(value?: string | null): string {
  if (!value) return "";
  // Preserve an already-valid IANA zone. Replacing Phoenix with Denver,
  // Mexico City with Chicago, or Brisbane with Sydney changes the local time
  // during parts of the year because those regions follow different DST rules.
  if (value.includes("/")) return value;
  return timezoneGroup(value)?.value ?? value;
}

export function formatTimezoneLabel(value?: string | null): string {
  if (!value) return "";
  return timezoneGroup(value)?.code ?? value.replace(/^.*\//, "").replace(/_/g, " ").toUpperCase();
}

export // Expand short labels (e.g. "IST") into the matching IANA names
// (e.g. "Asia/Kolkata", "Asia/Calcutta") plus the label itself, so the
// backend's case-insensitive IN check matches contacts however their
// timezone happens to be stored.
function expandTimezoneFilter(values: string[]): string[] {
  const set = new Set<string>();
  for (const value of values) {
    set.add(value);
    const group = timezoneGroup(value);
    if (!group) continue;
    set.add(group.value);
    group.zones.forEach((zone) => set.add(zone));
    group.aliases.forEach((alias) => set.add(alias));
  }
  return Array.from(set);
}
