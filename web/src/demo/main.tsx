import { useState } from "react";
import { createRoot } from "react-dom/client";

import { AppShell, Workspace } from "../components/AppShell";
import { OperatePage, SettingsPage, ShowsPage } from "../App";
import { InstallationPage } from "../pages/InstallationPage";
import { OverviewPage } from "../pages/OverviewPage";
import "../styles.css";
import {
  demoInstallation,
  demoInstallations,
  demoActivity,
  demoRuntime,
  demoProjects,
  demoSession,
  demoTopology,
} from "./overview-fixtures";

function VisualReviewFixture() {
  const [workspace, setWorkspace] = useState<Workspace>("overview");
  const [setupSection, setSetupSection] = useState<"photos" | "devices" | "inputs">("photos");
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
        runtimeFreshness="fresh"
        topologyFreshness="fresh"
        refreshing={false}
        onNavigate={(next) => {
          if (next === "installation") setSetupSection("photos");
          setWorkspace(next);
        }}
        onRefresh={() => undefined}
        onSignOut={() => undefined}
      >
        {workspace === "installation" ? (
          <InstallationPage
            initialSection={setupSection}
            installation={demoInstallation}
            topology={demoTopology}
            installationError={false}
            topologyError={false}
            loading={false}
            session={demoSession}
            onRefresh={() => undefined}
          />
        ) : workspace === "operate" ? (
          <OperatePage
            session={demoSession}
            installation={demoInstallation}
            runtime={demoRuntime}
            topology={demoTopology}
            runtimeError={false}
            runtimeFreshness="fresh"
            loading={false}
            onRefresh={async () => undefined}
          />
        ) : workspace === "settings" ? (
          <SettingsPage
            session={demoSession}
            installations={demoInstallations}
            onRefresh={async () => undefined}
          />
        ) : workspace === "shows" ? (
          <ShowsPage session={demoSession} initialProjects={demoProjects}
            onInputs={() => { setSetupSection("inputs"); setWorkspace("installation"); }} />
        ) : (
          <OverviewPage
            onManageDevices={() => { setSetupSection("devices"); setWorkspace("installation"); }}
            installation={demoInstallation}
            runtime={demoRuntime}
            topology={demoTopology}
            runtimeFreshness="fresh"
            topologyFreshness="fresh"
            activity={demoActivity}
            installationError={false}
            runtimeError={false}
            loading={false}
            onNavigate={(next) => {
              if (next === "installation") setSetupSection("photos");
              setWorkspace(next);
            }}
          />
        )}
      </AppShell>
    </>
  );
}

createRoot(document.getElementById("root")!).render(
  <VisualReviewFixture />,
);
