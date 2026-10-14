import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";

import { AppShell, Workspace } from "../components/AppShell";
import { OperatePage, SettingsPage } from "../App";
import { InstallationPage } from "../pages/InstallationPage";
import { OverviewPage } from "../pages/OverviewPage";
import "../styles.css";
import {
  demoInstallation,
  demoInstallations,
  demoActivity,
  demoRuntime,
  demoSession,
  demoTopology,
} from "./overview-fixtures";

function VisualReviewFixture() {
  const [workspace, setWorkspace] = useState<Workspace>("overview");
  return (
    <>
      <div className="demo-banner" role="note">
        Isolated visual review fixture — not production status
      </div>
      <AppShell
        session={demoSession}
        workspace={workspace}
        installation={demoInstallation}
        installations={demoInstallations}
        runtime={demoRuntime}
        runtimeError={false}
        refreshing={false}
        onNavigate={setWorkspace}
        onRefresh={() => undefined}
        onSignOut={() => undefined}
      >
        {workspace === "installation" ? (
          <InstallationPage
            installation={demoInstallation}
            topology={demoTopology}
            installationError={false}
            topologyError={false}
            loading={false}
          />
        ) : workspace === "operate" ? (
          <OperatePage
            session={demoSession}
            installation={demoInstallation}
            runtime={demoRuntime}
            topology={demoTopology}
            runtimeError={false}
            loading={false}
            onRefresh={async () => undefined}
          />
        ) : workspace === "settings" ? (
          <SettingsPage
            session={demoSession}
            installations={demoInstallations}
            onRefresh={async () => undefined}
          />
        ) : (
          <OverviewPage
            installation={demoInstallation}
            runtime={demoRuntime}
            activity={demoActivity}
            installationError={false}
            runtimeError={false}
            loading={false}
          />
        )}
      </AppShell>
    </>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <VisualReviewFixture />
  </StrictMode>,
);
