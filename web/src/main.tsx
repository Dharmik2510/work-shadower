import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/hanken-grotesk";
import "./styles/tokens.css";
import "./styles/app.css";
import App from "./App";
import { initApi } from "./api";

initApi().then(() => {
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
});
