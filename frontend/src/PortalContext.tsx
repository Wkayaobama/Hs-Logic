import { createContext, useContext } from "react";
import type { PortalInfo } from "./api/types";

export const PortalContext = createContext<PortalInfo | null>(null);

/** Access the currently loaded portal info. Must be used within <App>, after
 * portal data has successfully loaded (App blocks rendering of tabs until
 * then), so this never returns null in practice. */
export function usePortal(): PortalInfo {
  const portal = useContext(PortalContext);
  if (!portal) {
    throw new Error("usePortal() must be used within a loaded PortalContext");
  }
  return portal;
}
