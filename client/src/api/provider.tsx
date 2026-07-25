import { createContext, type ReactNode, useContext } from "react";

import type { RelayApi } from "./relay-api";

const RelayApiContext = createContext<RelayApi | null>(null);

export function RelayApiProvider({
  api,
  children,
}: {
  api: RelayApi;
  children: ReactNode;
}) {
  return (
    <RelayApiContext.Provider value={api}>
      {children}
    </RelayApiContext.Provider>
  );
}

export function useRelayApi(): RelayApi {
  const api = useContext(RelayApiContext);
  if (api === null) {
    throw new Error("RelayApiProvider is missing.");
  }
  return api;
}
