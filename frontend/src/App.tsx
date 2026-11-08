import { Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { ToastProvider } from "./components/Toast";
import { Overview } from "./pages/Overview";
import { VideoAnalysis } from "./pages/VideoAnalysis";
import { LiveMonitoring } from "./pages/LiveMonitoring";
import { ZoneEditor } from "./pages/ZoneEditor";
import { Incidents } from "./pages/Incidents";
import { SettingsPage } from "./pages/Settings";

export default function App() {
  return (
    <ToastProvider>
      <AppShell>
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/analysis" element={<VideoAnalysis />} />
          <Route path="/live" element={<LiveMonitoring />} />
          <Route path="/zones" element={<ZoneEditor />} />
          <Route path="/incidents" element={<Incidents />} />
          <Route path="/settings" element={<SettingsPage />} />
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
