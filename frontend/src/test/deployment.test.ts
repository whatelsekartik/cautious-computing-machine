import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../lib/api";
import {
  deployment,
  loadDeployment,
  resetDeployment,
  deploymentMode,
  isCloud,
} from "../lib/deployment";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const CLOUD_CONFIG = {
  mode: "cloud",
  app: { name: "CivicWatch", version: "1.0.0" },
  auth_required: true,
  deployment: { database: "postgres" as unknown as string },
  capabilities: {
    local_inference: false,
    live_capture: false,
    video_upload: true,
    evidence_redirect: true,
    connector: true,
    exports: true,
  },
  limits: { max_upload_bytes: 4, max_json_request_bytes: 4, function_timeout_s: 60, incidents_per_batch: 50 },
};

beforeEach(() => {
  resetDeployment();
});

afterEach(() => {
  vi.restoreAllMocks();
  resetDeployment();
});

describe("deployment detection", () => {
  it("falls back to local when the endpoint does not exist", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("not found", { status: 404 })));
    const config = await loadDeployment();
    expect(config.mode).toBe("local");
    expect(config.capabilities.local_inference).toBe(true);
    expect(deploymentMode()).toBe("local");
    expect(isCloud()).toBe(false);
  });

  it("uses the cloud config served by Vercel", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(CLOUD_CONFIG)));
    const config = await loadDeployment();
    expect(config.mode).toBe("cloud");
    expect(config.auth_required).toBe(true);
    expect(config.capabilities.local_inference).toBe(false);
    expect(isCloud()).toBe(true);
    expect(deployment().limits.function_timeout_s).toBe(60);
  });

  it("fetches the configuration only once", async () => {
    const spy = vi.fn(async () => json(CLOUD_CONFIG));
    vi.stubGlobal("fetch", spy);
    await Promise.all([loadDeployment(), loadDeployment()]);
    await loadDeployment();
    expect(spy).toHaveBeenCalledTimes(1);
  });
});

describe("cloud request headers", () => {
  it("adds the CSRF header to cookie-authenticated writes only", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(CLOUD_CONFIG)));
    await loadDeployment();

    const spy = vi.fn(async (_url: string, _init?: RequestInit) => json({ values: {} }));
    vi.stubGlobal("fetch", spy);

    await api.updateSettings({ confidence: 0.5 });
    const write = spy.mock.calls[0][1] as RequestInit;
    expect((write.headers as Record<string, string>)["X-CivicWatch-CSRF"]).toBe("1");
    expect(write.credentials).toBe("same-origin");

    spy.mockClear();
    await api.settings();
    const read = spy.mock.calls[0][1] as RequestInit;
    expect((read.headers as Record<string, string>)["X-CivicWatch-CSRF"]).toBeUndefined();
  });

  it("does not send the CSRF header in local mode", async () => {
    const spy = vi.fn(async (_url: string, _init?: RequestInit) => json({ values: {} }));
    vi.stubGlobal("fetch", spy);
    await api.updateSettings({ confidence: 0.5 });
    const init = spy.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>)["X-CivicWatch-CSRF"]).toBeUndefined();
  });
});

describe("direct uploads", () => {
  it("asks for a presigned URL, PUTs the file and marks it ready", async () => {
    const calls: { url: string; method: string; body?: unknown }[] = [];
    const ticket = {
      media_id: "media-1",
      key: "media/media-1/source.mp4",
      upload: {
        url: "https://storage.example.com/bucket/media/media-1/source.mp4?X-Amz-Signature=abc",
        method: "PUT",
        headers: { "Content-Type": "video/mp4" },
        key: "media/media-1/source.mp4",
        expires_in: 900,
      },
    };

    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url, method: init?.method || "GET", body: init?.body });
        if (url === "/api/media/upload-url") return json(ticket);
        if (url === "/api/media/media-1/complete") return json({ media_id: "media-1", status: "ready" });
        return json({ detail: "unexpected" }, 404);
      }),
    );

    const xhr = {
      upload: {} as { onprogress?: (event: { lengthComputable: boolean; loaded: number; total: number }) => void },
      status: 200,
      open: vi.fn(),
      setRequestHeader: vi.fn(),
      send: vi.fn(function (this: typeof xhr) {
        this.upload.onprogress?.({ lengthComputable: true, loaded: 5, total: 10 });
        this.onload?.();
      }),
      onload: undefined as undefined | (() => void),
      onerror: undefined as undefined | (() => void),
    };
    vi.stubGlobal("XMLHttpRequest", vi.fn(() => xhr));

    const progress: number[] = [];
    const file = new File([new Uint8Array([1, 2, 3])], "street.mp4", { type: "video/mp4" });
    const result = await api.media.uploadDirect(file, (value) => progress.push(value));

    expect(result.status).toBe("ready");
    expect(xhr.open).toHaveBeenCalledWith("PUT", ticket.upload.url);
    expect(xhr.setRequestHeader).toHaveBeenCalledWith("Content-Type", "video/mp4");
    expect(progress).toEqual([0.5]);
    const payload = JSON.parse(String(calls[0].body));
    expect(payload).toMatchObject({ filename: "street.mp4", content_type: "video/mp4", size_bytes: 3 });
    // The file itself never goes through the API request.
    expect(calls.every((call) => call.url.startsWith("/api/"))).toBe(true);
  });
});
