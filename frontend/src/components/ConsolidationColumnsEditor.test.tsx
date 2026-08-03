import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ConsolidationColumnsEditor, type ConsolidationColumnsDraft } from "./ConsolidationColumnsEditor";

function Harness({ initial }: { initial: ConsolidationColumnsDraft }) {
  const [value, setValue] = useState(initial);
  return <ConsolidationColumnsEditor kind="artist" value={value} onChange={setValue} onSave={vi.fn()} onCancel={vi.fn()} search={async () => []} />;
}

describe("ConsolidationColumnsEditor", () => {
  it("removing an MBID clears its attached mask and target names", async () => {
    const user = userEvent.setup();
    render(<Harness initial={{
      maskName: "BeyoncÃ©",
      mbids: ["mbid-a"],
      targets: [
        { rawName: "BeyoncÃ©", mbid: "mbid-a" },
        { rawName: "Beyonce", mbid: "mbid-a" },
        { rawName: "Independent", mbid: "mbid-b" },
      ],
    }} />);

    await user.click(screen.getByRole("button", { name: "Remove MBID mbid-a" }));

    expect(screen.getByPlaceholderText("Visible artist name")).toHaveValue("");
    expect(screen.queryByText("Beyonce")).not.toBeInTheDocument();
    expect(screen.getByText("Independent")).toBeInTheDocument();
  });

  it("removing the final name attached to an MBID clears that MBID", async () => {
    const user = userEvent.setup();
    render(<Harness initial={{
      maskName: "Display name",
      mbids: ["mbid-a"],
      targets: [{ rawName: "Raw name", mbid: "mbid-a" }],
    }} />);

    await user.click(screen.getByRole("button", { name: "Remove target Raw name" }));

    expect(screen.queryByText("mbid-a")).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText("Visible artist name")).toHaveValue("Display name");
  });
});
