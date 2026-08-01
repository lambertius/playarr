import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RenamePreviewDialog, type RenamePreviewPlan } from "./RenamePreviewDialog";

const plan: RenamePreviewPlan = {
  old_folder: "C:\\Library\\Artist\\Old Name",
  new_folder: "D:\\Library\\Artist\\New Name",
  collisions: [],
  active_stream_usage: true,
  cross_volume: true,
  case_only: false,
  steps: [
    {
      role: "video",
      source: "C:\\Library\\Artist\\Old Name\\Old Name.mkv",
      destination: "D:\\Library\\Artist\\New Name\\New Name.mkv",
      size_bytes: 123,
    },
    {
      role: "playarr_sidecar",
      source: "C:\\Library\\Artist\\Old Name\\Old Name.playarr.xml",
      destination: "D:\\Library\\Artist\\New Name\\New Name.playarr.xml",
      size_bytes: 45,
    },
  ],
};

describe("RenamePreviewDialog", () => {
  it("shows the exact journal plan and safety warnings before commit", () => {
    const rename = vi.fn();
    render(
      <RenamePreviewDialog
        plan={plan}
        isPending={false}
        onRename={rename}
        onClose={() => undefined}
      />,
    );

    expect(screen.getByText("Old Name")).toBeInTheDocument();
    expect(screen.getByText("New Name")).toBeInTheDocument();
    expect(screen.getByText(/Cross-volume files/)).toBeInTheDocument();
    expect(screen.getByText(/Playback will be stopped/)).toBeInTheDocument();
    expect(screen.getByText(plan.steps[0].destination)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Rename 2 files/ }));
    expect(rename).toHaveBeenCalledOnce();
  });

  it("blocks commit when the planner found a collision", () => {
    render(
      <RenamePreviewDialog
        plan={{ ...plan, collisions: [{
          source: plan.steps[0].source,
          destination: plan.steps[0].destination,
          reason: "destination_exists",
        }] }}
        isPending={false}
        onRename={() => undefined}
        onClose={() => undefined}
      />,
    );

    expect(screen.getByText(/1 destination collision/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Rename 2 files/ })).toBeDisabled();
  });
});
