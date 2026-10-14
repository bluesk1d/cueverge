import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";

import { AppShell, Workspace } from "../components/AppShell";
import { InstallationPage } from "../pages/InstallationPage";
import { OverviewPage } from "../pages/OverviewPage";
import "../styles.css";
import {
  demoInstallation,
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
        ) : (
          <OverviewPage
            installation={demoInstallation}
            runtime={demoRuntime}
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
