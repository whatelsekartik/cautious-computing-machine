import { Laptop, ShieldCheck } from "lucide-react";
import { PageHeader } from "../components/AppShell";
import { Card, Note } from "../components/ui";

/**
 * Stand-in for pages that need hardware the hosted deployment does not have
 * (webcams, RTSP capture, the zone canvas). Explaining the split beats a page
 * full of failed requests.
 */
export function LocalOnly({ title, description }: { title: string; description: string }) {
  return (
    <div>
      <PageHeader eyebrow="Local-only feature" title={title} description={description} />
      <Card className="max-w-2xl">
        <div className="flex items-start gap-3">
          <Laptop size={20} className="mt-0.5 text-teal" />
          <div className="space-y-2 text-sm leading-relaxed text-muted">
            <p>
              Inference, camera capture and zone editing run in the standalone app on your Mac.
              The cloud deployment stores incidents, evidence and reviews only.
            </p>
            <p>
              Start the local app with <span className="font-mono text-[0.78rem]">Start CivicWatch.command</span>,
              or keep the connector running with{" "}
              <span className="font-mono text-[0.78rem]">Start Cloud Connector.command</span> to send
              incidents here.
            </p>
          </div>
        </div>
        <div className="mt-4">
          <Note tone="success" icon={ShieldCheck}>
            Camera URLs and credentials are encrypted on the Mac and are never uploaded.
          </Note>
        </div>
      </Card>
    </div>
  );
}
