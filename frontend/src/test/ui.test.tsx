import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { Badge, Modal, ProgressBar, StatusBadge, Toggle } from "../components/ui";
import { ToastProvider } from "../components/Toast";

describe("UI primitives", () => {
  it("renders status badges with readable labels", () => {
    render(<StatusBadge status="pending_review" />);
    expect(screen.getByText("Pending review")).toBeInTheDocument();
  });

  it("applies tone classes to badges", () => {
    const { container } = render(<Badge tone="danger">high</Badge>);
    expect(container.firstChild).toHaveClass("text-danger");
  });

  it("reflects the progress value in the bar width", () => {
    const { container } = render(<ProgressBar value={0.42} />);
    const bar = container.firstElementChild?.firstElementChild;
    expect(bar?.getAttribute("style")).toContain("width: 42%");
  });

  it("toggles state when clicked", () => {
    const onChange = vi.fn();
    render(<Toggle checked={false} onChange={onChange} label="Store clips" />);
    const control = screen.getByRole("switch", { name: "Store clips" });
    expect(control).toHaveAttribute("aria-checked", "false");
    fireEvent.click(control);
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("renders a modal only when open", () => {
    const { rerender } = render(
      <Modal open={false} onClose={() => undefined} title="Hidden">
        body
      </Modal>,
    );
    expect(screen.queryByText("Hidden")).not.toBeInTheDocument();
    rerender(
      <Modal open onClose={() => undefined} title="Visible">
        body
      </Modal>,
    );
    expect(screen.getByText("Visible")).toBeInTheDocument();
    expect(screen.getByText("body")).toBeInTheDocument();
  });

  it("renders toast notifications with their message", () => {
    render(
      <ToastProvider>
        <div>child</div>
      </ToastProvider>,
    );
    expect(screen.getByText("child")).toBeInTheDocument();
  });
});
