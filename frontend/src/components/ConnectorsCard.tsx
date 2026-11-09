import { useEffect, useState } from "react";
import { Copy, KeyRound, Laptop, Loader2, Plus, Trash2 } from "lucide-react";
import { Badge, Button, Card, EmptyState, Note, SectionHeader, TextInput } from "./ui";
import { api } from "../lib/api";
import { useToast } from "./Toast";
import { formatDateTime } from "../lib/format";
import type { Connector } from "../lib/types";

/**
 * Connector pairing for the cloud dashboard.
 *
 * A connector token is displayed exactly once, at creation. Only its SHA-256
 * hash is stored, so a lost token means creating a new connector — the same
 * model GitHub uses for personal access tokens.
 */
export function ConnectorsCard() {
  const toast = useToast();
  const [items, setItems] = useState<Connector[]>([]);
  const [loading, setLoading] = useState(true);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [issued, setIssued] = useState<Connector | null>(null);

  const load = async () => {
    try {
      const response = await api.connectors.list();
      setItems(response.items);
    } catch (problem) {
      toast.push(problem instanceof Error ? problem.message : "Could not load connectors", "error");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 15000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const create = async () => {
    if (!name.trim()) return;
    setBusy(true);
    try {
      const connector = await api.connectors.create({ name: name.trim() });
      setIssued(connector);
      setName("");
      await load();
    } catch (problem) {
      toast.push(problem instanceof Error ? problem.message : "Could not create connector", "error");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (connector: Connector) => {
    if (!window.confirm(`Revoke "${connector.name}"? It will stop syncing immediately.`)) return;
    try {
      await api.connectors.remove(connector.id);
      if (issued?.id === connector.id) setIssued(null);
      await load();
      toast.push("Connector revoked", "success");
    } catch (problem) {
      toast.push(problem instanceof Error ? problem.message : "Could not revoke connector", "error");
    }
  };

  const copy = async (value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      toast.push("Token copied — store it somewhere safe, it will not be shown again", "success");
    } catch {
      toast.push("Copy failed — select the token manually", "error");
    }
  };

  return (
    <Card className="mb-4">
      <SectionHeader
        icon={Laptop}
        title="Local connectors"
        subtitle="Each connector runs inference on your own hardware. Only incidents, metadata and selected evidence travel over HTTPS."
        action={<Badge tone={items.some((item) => item.online) ? "positive" : "neutral"}>{items.some((item) => item.online) ? "online" : "no connector online"}</Badge>}
      />

      <div className="flex flex-wrap items-end gap-2">
        <label className="min-w-[220px] flex-1">
          <span className="mb-1 block text-[0.7rem] font-medium uppercase tracking-[0.08em] text-muted">
            New connector name
          </span>
          <TextInput
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="e.g. Ward office iMac"
            onKeyDown={(event) => event.key === "Enter" && void create()}
          />
        </label>
        <Button icon={busy ? Loader2 : Plus} disabled={busy || !name.trim()} onClick={() => void create()}>
          Create token
        </Button>
      </div>

      {issued?.token ? (
        <div className="mt-3">
          <Note tone="warning" icon={KeyRound}>
            <span className="block font-medium">Copy this token now — it is shown only once.</span>
            <span className="mt-1 block break-all font-mono text-[0.72rem]">{issued.token}</span>
            <span className="mt-1 flex flex-wrap gap-2">
              <Button size="sm" variant="ghost" icon={Copy} onClick={() => void copy(issued.token || "")}>
                Copy token
              </Button>
              <span className="self-center text-[0.7rem] text-muted">
                Then run <span className="font-mono">Start Cloud Connector.command</span> on that Mac.
              </span>
            </span>
          </Note>
        </div>
      ) : null}

      <div className="mt-3">
        {loading ? (
          <p className="flex items-center gap-2 py-4 text-[0.8rem] text-muted">
            <Loader2 size={14} className="animate-spin" /> Loading connectors…
          </p>
        ) : items.length ? (
          <ul className="divide-y divide-line/70">
            {items.map((connector) => (
              <li key={connector.id} className="flex flex-wrap items-center gap-2 py-2">
                <span
                  className={`h-1.5 w-1.5 rounded-full ${connector.online ? "bg-positive" : "bg-line"}`}
                  aria-hidden
                />
                <span className="text-[0.82rem] font-medium text-ink">{connector.name}</span>
                <Badge tone={connector.online ? "positive" : "neutral"}>
                  {connector.online ? "online" : "offline"}
                </Badge>
                {connector.device ? <Badge tone="teal">{connector.device}</Badge> : null}
                <span className="text-[0.7rem] text-muted">
                  {connector.last_seen_at
                    ? `last seen ${formatDateTime(connector.last_seen_at)}`
                    : "never connected"}
                </span>
                <span className="ml-auto flex items-center gap-1.5">
                  <Button
                    size="sm"
                    variant="ghost"
                    icon={Trash2}
                    onClick={() => void remove(connector)}
                    aria-label={`Revoke ${connector.name}`}
                  >
                    Revoke
                  </Button>
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState
            icon={Laptop}
            title="No connectors yet"
            description="Create a token, then start the connector on the Mac that has the cameras. Inference stays there."
          />
        )}
      </div>

      <p className="mt-2 text-[0.68rem] leading-relaxed text-muted">
        Tokens are stored as SHA-256 hashes only. Revoking a connector invalidates its token
        immediately; incidents it already pushed stay in the dashboard.
      </p>
    </Card>
  );
}
