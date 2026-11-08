import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../lib/api";

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

afterEach(() => {
  vi.restoreAllMocks();
});

describe("api client", () => {
  it("builds incident export URLs with filters", () => {
    expect(api.incidents.exportPdfUrl({ status: "detected" })).toBe(
      "/api/incidents/export.pdf?status=detected",
    );
    expect(api.incidents.exportCsvUrl({ ids: "INC-1,INC-2" })).toContain("ids=INC-1%2CINC-2");
  });

  it("builds zone and stream URLs", () => {
    expect(api.jobs.streamUrl("job-1")).toBe("/api/jobs/job-1/stream");
    expect(api.cameras.frameUrl("cam-1")).toContain("/api/cameras/cam-1/frame?annotated=false");
  });

  it("surfaces backend error details", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ detail: "Model is not installed" }, 400)),
    );
    await expect(api.jobs.create({ media_id: "m1" })).rejects.toMatchObject({
      message: "Model is not installed",
      status: 400,
    });
    await expect(api.jobs.create({ media_id: "m1" })).rejects.toBeInstanceOf(ApiError);
  });

  it("sends JSON bodies for settings updates", async () => {
    const spy = vi.fn(async (_url: string, init?: RequestInit) =>
      jsonResponse({ values: JSON.parse(String(init?.body)) }),
    );
    vi.stubGlobal("fetch", spy);
    const result = await api.updateSettings({ confidence: 0.6 });
    expect(result.values).toMatchObject({ confidence: 0.6 });
    const init = spy.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("PATCH");
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  });
});
