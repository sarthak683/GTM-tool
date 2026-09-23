import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import PagedCards from "./PagedCards";

describe("PagedCards", () => {
  it("mounts only one page and keeps the final record reachable", () => {
    const mounted = vi.fn();
    function Card({ index }: { index: number }) { mounted(index); return <div>Deal {index}</div>; }
    render(<PagedCards pageSize={12}>{Array.from({ length: 25 }, (_, i) => <Card key={i} index={i} />)}</PagedCards>);
    expect(mounted).toHaveBeenCalledTimes(12);
    expect(screen.queryByText("Deal 24")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("Deal 24")).toBeInTheDocument();
    expect(screen.queryByText("Deal 0")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Previous" }));
    expect(screen.getByText("Deal 12")).toBeInTheDocument();
  });

  it("does not leave the list blank when filtering shrinks the result", () => {
    const cards = Array.from({ length: 25 }, (_, i) => <div key={i}>Deal {i}</div>);
    const { rerender } = render(<PagedCards>{cards}</PagedCards>);
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    rerender(<PagedCards>{cards.slice(0, 1)}</PagedCards>);
    expect(screen.getByText("Deal 0")).toBeInTheDocument();
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  });
});
