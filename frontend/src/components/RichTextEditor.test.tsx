import { StrictMode } from "react";
import { fireEvent, render, waitFor } from "@testing-library/react";
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


it("saves a cleared note as an empty value, not an HTML paragraph", async () => {
  const onChange = vi.fn();
  const { container, rerender } = render(<RichTextEditor value="Original note" onChange={onChange} />);
  await waitFor(() => expect(container.querySelector('.tiptap')).toHaveTextContent('Original note'));
  rerender(<RichTextEditor value="" onChange={onChange} />);
  await waitFor(() => expect(container.querySelector('.tiptap')).toHaveTextContent(''));
  const editable = container.querySelector('.tiptap')!;
  fireEvent.focus(editable);
  fireEvent.blur(editable);
  await waitFor(() => expect(onChange).toHaveBeenCalledWith(""));
});

it("keeps formatting when saving a nonempty note", async () => {
  const onChange = vi.fn();
  const { container } = render(<RichTextEditor value="<p><strong>Important</strong></p>" onChange={onChange} />);
  await waitFor(() => expect(container.querySelector('.tiptap')).toHaveTextContent('Important'));
  const editable = container.querySelector('.tiptap')!;
  fireEvent.focus(editable);
  fireEvent.blur(editable);
  await waitFor(() => expect(onChange).toHaveBeenCalledWith('<p><strong>Important</strong></p>'));
});
