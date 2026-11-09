import { useEffect, useState } from "react";
import { KeyRound, Loader2, ShieldCheck } from "lucide-react";
import { BrandMark } from "./Accents";
import { Button, Card, Note, TextInput } from "./ui";
import { api } from "../lib/api";
import { deployment } from "../lib/deployment";

/**
 * Cloud deployments protect the dashboard with a signed session cookie.
 * The token is the operator's `CIVICWATCH_ADMIN_TOKEN`, never stored in the
 * bundle: it is exchanged once for an HttpOnly cookie and then forgotten.
 */
export function LoginGate({ onSuccess }: { onSuccess: () => void }) {
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const config = deployment();

  useEffect(() => {
    document.title = "Sign in · CivicWatch";
  }, []);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!token.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.auth.login(token.trim());
      setToken("");
      onSuccess();
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "Sign-in failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-canvas px-4 py-10">
      <div className="w-full max-w-md">
        <div className="mb-6 flex items-center gap-3">
          <BrandMark size={38} />
          <div>
            <p className="text-lg font-semibold leading-tight tracking-tight text-ink">CivicWatch</p>
            <p className="text-[0.68rem] uppercase tracking-[0.14em] text-muted">
              {config.app.name} · cloud dashboard
            </p>
          </div>
        </div>
        <Card className="p-6">
          <form onSubmit={submit} className="space-y-4">
            <div>
              <h1 className="text-base font-semibold text-ink">Sign in to the dashboard</h1>
              <p className="mt-1 text-sm leading-relaxed text-muted">
                Use the operator token configured for this deployment
                (<code className="rounded bg-surface-muted px-1 py-0.5 text-[0.72rem]">CIVICWATCH_ADMIN_TOKEN</code>).
                It is exchanged for a 12-hour session cookie.
              </p>
            </div>
            <label className="block">
              <span className="mb-1 block text-[0.72rem] font-medium uppercase tracking-[0.08em] text-muted">
                Operator token
              </span>
              <TextInput
                type="password"
                autoFocus
                autoComplete="current-password"
                value={token}
                onChange={(event) => setToken(event.target.value)}
                placeholder="Paste the admin token"
              />
            </label>
            {error ? (
              <Note tone="danger" icon={KeyRound}>
                {error}
              </Note>
            ) : null}
            <Button type="submit" disabled={busy || !token.trim()} className="w-full justify-center">
              {busy ? <Loader2 size={15} className="animate-spin" /> : <ShieldCheck size={15} />}
              {busy ? "Signing in…" : "Sign in"}
            </Button>
          </form>
        </Card>
        <p className="mt-4 flex items-start gap-2 text-[0.72rem] leading-relaxed text-muted">
          <ShieldCheck size={13} className="mt-0.5 shrink-0" />
          Footage stays in your private storage. Camera credentials never reach this server —
          inference runs on a local connector you control.
        </p>
      </div>
    </div>
  );
}
