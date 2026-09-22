import { StrictMode } from "react";
import { render, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { RichTextEditor } from "./RichTextEditor";

it("survives strict lifecycle mounting and external content changes without saving", async () => {
  const onChange = vi.fn();
  const { container, rerender, unmount } = render(<StrictMode><RichTextEditor value="First note" onChange={onChange} /></StrictMode>);
  await waitFor(() => expect(container.querySelector('.tiptap')).toHaveTextContent('First note'));
  rerender(<StrictMode><RichTextEditor value="Second note" onChange={onChange} /></StrictMode>);
  await waitFor(() => expect(container.querySelector('.tiptap')).toHaveTextContent('Second note'));
  expect(onChange).not.toHaveBeenCalled();
  unmount();
});
