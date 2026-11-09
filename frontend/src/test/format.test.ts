import { describe, expect, it } from "vitest";
import {
  categoryLabel,
  formatBytes,
  formatDateTime,
  formatDuration,
  percent,
  statusLabel,
} from "../lib/format";

describe("formatting helpers", () => {
  it("formats durations for both short and long videos", () => {
    expect(formatDuration(0)).toBe("0:00");
    expect(formatDuration(65)).toBe("1:05");
    expect(formatDuration(3725)).toBe("1:02:05");
    expect(formatDuration(null)).toBe("—");
  });

  it("formats byte sizes", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(1024)).toBe("1.0 KB");
    expect(formatBytes(1024 * 1024 * 4.5)).toBe("4.5 MB");
  });

  it("maps backend status and category codes to human labels", () => {
    expect(statusLabel("pending_review")).toBe("Pending review");
    expect(statusLabel("detected")).toBe("Detected");
    expect(categoryLabel("illegal_parking")).toBe("Potential illegal parking");
    expect(categoryLabel("unknown_code")).toBe("unknown code");
  });

  it("formats percentages and timestamps safely", () => {
    expect(percent(0.426)).toBe("43%");
    expect(formatDateTime("2026-03-04T09:15:00+00:00")).toMatch(/2026/);
    expect(formatDateTime("not-a-date")).toBe("not-a-date");
    expect(formatDateTime(null)).toBe("—");
  });
});
