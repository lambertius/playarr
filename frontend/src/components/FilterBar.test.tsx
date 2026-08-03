import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { FilterBar } from "./FilterBar";

describe("FilterBar", () => {
  it("treats page-specific filters as part of the same clear-all surface", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const onClearAll = vi.fn();
    render(
      <FilterBar
        filters={{ artist: "Bowie" }}
        onChange={onChange}
        additionalActiveCount={2}
        onClearAll={onClearAll}
      >
        <label>AI status <select aria-label="AI status"><option>Partial</option></select></label>
      </FilterBar>,
    );

    expect(screen.getByText("Filters (3)")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Clear all/ }));
    expect(onClearAll).toHaveBeenCalledOnce();
    expect(onChange).not.toHaveBeenCalled();
  });
});
