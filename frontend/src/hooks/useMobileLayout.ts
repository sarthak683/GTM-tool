import { useEffect, useState } from "react";

// Matches index.css. CSS display:none still mounts every hidden React card.
export function useMobileLayout() {
  const [mobile, setMobile] = useState(() => window.matchMedia("(max-width: 768px)").matches);
  useEffect(() => {
    const query = window.matchMedia("(max-width: 768px)");
    const update = () => setMobile(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return mobile;
}
