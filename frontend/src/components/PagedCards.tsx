import { useState, type ReactNode } from "react";

/** Keep large boards bounded without dropping records from search or exports. */
export default function PagedCards({ children, pageSize = 12 }: { children: ReactNode[]; pageSize?: number }) {
  const [requestedPage, setPage] = useState(0);
  const pages = Math.max(1, Math.ceil(children.length / pageSize));
  const page = Math.min(requestedPage, pages - 1);
  return <>
    {children.slice(page * pageSize, (page + 1) * pageSize)}
    {pages > 1 && <nav aria-label="Card pages" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6, flexShrink: 0, padding: "8px 0" }}>
      <button type="button" className="crm-button soft" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
      <span style={{ fontSize: 11, color: "#64748b" }}>{page * pageSize + 1}–{Math.min((page + 1) * pageSize, children.length)} of {children.length}</span>
      <button type="button" className="crm-button soft" disabled={page === pages - 1} onClick={() => setPage(page + 1)}>Next</button>
    </nav>}
  </>;
}
