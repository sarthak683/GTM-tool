import type { CSSProperties } from "react";

/**
 * Shimmer placeholder block. Drop in wherever a "Loading…" string used to be,
 * sized to the content it stands in for. Styling lives in index.css
 * (.crm-skeleton) so the shimmer + reduced-motion behaviour stay consistent.
 *
 *   <Skeleton width={180} height={16} />
 *   <Skeleton height={120} radius={14} />
 */
export function Skeleton({
  width = "100%",
  height = 14,
  radius = 8,
  style,
}: {
  width?: number | string;
  height?: number | string;
  radius?: number;
  style?: CSSProperties;
}) {
  return (
    <span
      aria-hidden="true"
      className="crm-skeleton"
      style={{ display: "block", width, height, borderRadius: radius, ...style }}
    />
  );
}

/**
 * A column of N skeleton "rows", each a header bar + a couple of lighter lines —
 * a reasonable stand-in for list/table/card content while it loads.
 */
export function SkeletonList({
  rows = 5,
  gap = 14,
  style,
}: {
  rows?: number;
  gap?: number;
  style?: CSSProperties;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap, ...style }}>
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 14,
            padding: "12px 14px",
            border: "1px solid #eef2f7",
            borderRadius: 14,
            background: "#fff",
          }}
        >
          <Skeleton width={38} height={38} radius={10} />
          <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 8 }}>
            <Skeleton width="42%" height={13} />
            <Skeleton width="68%" height={10} />
          </div>
          <Skeleton width={70} height={26} radius={999} />
        </div>
      ))}
    </div>
  );
}
