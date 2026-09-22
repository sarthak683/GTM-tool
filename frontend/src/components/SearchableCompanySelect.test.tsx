import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import SearchableCompanySelect from "./SearchableCompanySelect";

vi.mock("../lib/api", () => ({
  companiesApi: { get: vi.fn().mockResolvedValue({ id: "saved", name: "Saved company" }), search: vi.fn().mockResolvedValue([{ id: "remote", name: "Remote company" }]) },
  accountSourcingApi: { listCompaniesPaginated: vi.fn().mockResolvedValue({ items: [] }) },
}));

describe("SearchableCompanySelect", () => {
  it("resolves an existing value without downloading the catalog", async () => {
    render(<SearchableCompanySelect value="saved" onChange={() => {}} />);
    await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("Saved company"));
  });

  it("retains a remote selection after clearing another search", async () => {
    function Picker() {
      const [value, setValue] = useState<string>();
      return <SearchableCompanySelect value={value} onChange={setValue} />;
    }
    render(<Picker />);
    const input = screen.getByRole("textbox");
    const user = userEvent.setup();
    await user.click(input);
    await user.type(input, "Remote");
    await user.click(await screen.findByRole("button", { name: "Remote company" }));
    await user.click(input);
    await user.clear(input);
    fireEvent.mouseDown(document.body);
    expect(input).toHaveValue("Remote company");
  });
});
