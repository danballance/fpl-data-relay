import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { App } from "./App";
import { createRelayApi } from "./api/relay-api";
import { createAppQueryClient } from "./app/queryClient";
import "./styles.css";

const root = document.getElementById("root");
if (root === null) {
  throw new Error("Missing #root application element.");
}

const api = createRelayApi({
  baseUrl: "/api",
  fetchImplementation: window.fetch.bind(window),
});
const queryClient = createAppQueryClient();

createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <App api={api} queryClient={queryClient} />
    </BrowserRouter>
  </StrictMode>,
);
