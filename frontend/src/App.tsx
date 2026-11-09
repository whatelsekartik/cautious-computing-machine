import { useEffect, useState } from "react";
import { Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { LoginGate } from "./components/LoginGate";
import { ToastProvider } from "./components/Toast";
import { Overview } from "./pages/Overview";
import { VideoAnalysis } from "./pages/VideoAnalysis";
import { CloudUploads } from "./pages/CloudUploads";
import { LiveMonitoring } from "./pages/LiveMonitoring";
import { ZoneEditor } from "./pages/ZoneEditor";
import { Incidents } from "./pages/Incidents";
import { SettingsPage } from "./pages/Settings";
import { LocalOnly } from "./pages/LocalOnly";
import { api } from "./lib/api";
import { useDeployment } from "./lib/hooks";
import type { AuthStatus } from "./lib/types";

export default function App() {
  const deployment = useDeployment();
  const cloud = deployment.mode === "cloud";
  const [auth, setAuth] = useState<AuthStatus | null>(null);

  useEffect(() => {
    if (!cloud) {
      setAuth(null);
      return;
    }
    let active = true;
    api.auth
      .status()
      .then((status) => {
        if (active) setAuth(status);
      })
      .catch(() => {
        if (active) setAuth({ mode: "cloud", auth_required: true, authenticated: false });
      });
    return () => {
      active = false;
    };
  }, [cloud, deployment.auth_required]);

  if (cloud && deployment.auth_required) {
    if (!auth) {
      return (
        <div className="flex min-h-screen items-center justify-center bg-canvas text-sm text-muted">
          Checking your session…
        </div>
      );
    }
    if (!auth.authenticated) {
      return <LoginGate onSuccess={() => setAuth({ ...auth, authenticated: true })} />;
    }
  }

  return (
    <ToastProvider>
      <AppShell>
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/analysis" element={cloud ? <CloudUploads /> : <VideoAnalysis />} />
          <Route
            path="/live"
            element={
              cloud ? (
                <LocalOnly
                  title="Live monitoring runs on your Mac"
                  description="Camera capture and RTSP streams never touch Vercel. Open CivicWatch Studio on the machine with the cameras, or keep the connector running to feed this dashboard."
                />
              ) : (
                <LiveMonitoring />
              )
            }
          />
          <Route
            path="/zones"
            element={
              cloud ? (
                <LocalOnly
                  title="Zone drawing is a local editor"
                  description="Zones are stored with your Mac installation and applied by the local inference engine. Manage them in the standalone app; the incidents they produce appear here."
                />
              ) : (
                <ZoneEditor />
              )
            }
          />
          <Route path="/incidents" element={<Incidents />} />
          <Route path="/settings" element={<SettingsPage deployment={deployment} />} />
          <Route
            path="*"
            element={
              <div className="card p-8 text-center text-sm text-muted">
                That page does not exist. Use the sidebar to navigate.
              </div>
            }
          />
        </Routes>
      </AppShell>
    </ToastProvider>
  );
}
