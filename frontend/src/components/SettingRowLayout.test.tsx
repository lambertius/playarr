import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SettingRowLayout } from "./SettingRowLayout";

describe("SettingRowLayout", () => {
  it("keeps label, control and description in the shared responsive row", () => {
    const { container } = render(
      <SettingRowLayout label="Maximum downloads" description="Bounds concurrent acquisition." controlId="downloads">
        <input id="downloads" />
      </SettingRowLayout>,
    );
    expect(container.querySelector("[data-setting-row]")).toHaveClass("sm:grid-cols-[minmax(11rem,0.9fr)_minmax(12rem,1fr)_minmax(15rem,1.35fr)]");
    expect(screen.getByLabelText("Maximum downloads")).toBeInTheDocument();
    expect(screen.getByText("Bounds concurrent acquisition.").id).toBeTruthy();
  });
});
