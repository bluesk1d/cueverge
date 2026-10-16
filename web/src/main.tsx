import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { reportBrowserError } from "./api";
import "./styles.css";

window.addEventListener("error", (event) => {
  void reportBrowserError(
    event.message || "Unhandled browser error",
    event.filename || "window.error",
  ).catch(() => undefined);
});

window.addEventListener("unhandledrejection", (event) => {
  const message =
    event.reason instanceof Error ? event.reason.message : String(event.reason);
  void reportBrowserError(message, "unhandledrejection").catch(() => undefined);
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
