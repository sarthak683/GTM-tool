import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import MultiSelectFilter from "./MultiSelectFilter";


const options = [
  { value: "champion", label: "Champion" },
  { value: "buyer", label: "Economic Buyer" },
  { value: "evaluator", label: "Technical Evaluator" },
];

function Harness() {
  const [values, setValues] = useState<string[]>([]);
  return (
    <MultiSelectFilter
      values={values}
      onChange={setValues}
      options={options}
      label="Persona"
      allLabel="All Personas"
    />
  );
}

describe("MultiSelectFilter", () => {
  it("selects, deselects, clears all, searches, and closes outside", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(screen.getByRole("button", { name: "All Personas" }));
    await user.click(screen.getByLabelText("Champion"));
    expect(screen.getByRole("button", { name: "Champion" })).toBeInTheDocument();

    await user.click(screen.getByLabelText("Economic Buyer"));
    expect(screen.getByRole("button", { name: /2 selected/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "All Personas" }));
    expect(screen.getAllByRole("button", { name: "All Personas" })[0]).toBeInTheDocument();

    await user.type(screen.getByPlaceholderText("Search persona..."), "buyer");
    expect(screen.queryByLabelText("Champion")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Economic Buyer")).toBeInTheDocument();

    fireEvent.mouseDown(document.body);
    expect(screen.queryByPlaceholderText("Search persona...")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "All Personas" }));
    await user.click(screen.getByLabelText("Champion"));
    await user.click(screen.getByLabelText("Champion"));
    expect(screen.getAllByRole("button", { name: "All Personas" })[0]).toBeInTheDocument();
  });
});
