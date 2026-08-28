import { useState } from "react";
import { useApi } from "./hooks/useApi";
import type { PortalInfo } from "./api/types";
import { PortalContext } from "./PortalContext";
import Spinner from "./components/Spinner";
import ErrorState from "./components/ErrorState";
import TabNav, { type TabDef } from "./components/TabNav";
import {
  IconBuilding,
  IconDeal,
  IconPerson,
  IconShield,
  IconTicket,
  IconWarning,
} from "./components/icons";
import OverviewTab from "./tabs/OverviewTab";
import ContactsTab from "./tabs/ContactsTab";
import CompaniesTab from "./tabs/CompaniesTab";
import DealsTab from "./tabs/DealsTab";
import TicketsTab from "./tabs/TicketsTab";
import CrmHealthTab from "./tabs/CrmHealthTab";
import ContactHealthTab from "./tabs/ContactHealthTab";

const TABS: TabDef[] = [
  { id: "overview", label: "Overview" },
  { id: "contacts", label: "Contacts", icon: <IconPerson className="h-4 w-4" /> },
  { id: "companies", label: "Companies", icon: <IconBuilding className="h-4 w-4" /> },
  { id: "deals", label: "Deals", icon: <IconDeal className="h-4 w-4" /> },
  { id: "tickets", label: "Tickets", icon: <IconTicket className="h-4 w-4" /> },
  { id: "crm-health", label: "CRM Health", icon: <IconShield className="h-4 w-4" /> },
  { id: "contact-health", label: "Contact Health", icon: <IconWarning className="h-4 w-4" /> },
];

function renderTab(tabId: string) {
  switch (tabId) {
    case "overview":
      return <OverviewTab />;
    case "contacts":
      return <ContactsTab />;
    case "companies":
      return <CompaniesTab />;
    case "deals":
      return <DealsTab />;
    case "tickets":
      return <TicketsTab />;
    case "crm-health":
      return <CrmHealthTab />;
    case "contact-health":
      return <ContactHealthTab />;
    default:
      return null;
  }
}

export default function App() {
  const { data: portal, loading, error, reload } = useApi<PortalInfo>("/portal");
  const [activeTab, setActiveTab] = useState<string>("overview");

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-6xl mx-auto px-4 sm:px-6 py-6">
        <header className="flex items-center gap-4 mb-6">
          <div className="h-11 w-11 rounded-full bg-orange-500 text-white flex items-center justify-center font-semibold text-sm flex-shrink-0">
            HS
          </div>
          <div>
            <h1 className="text-lg font-semibold text-gray-900">
              {portal?.hub_name || "—"}
            </h1>
            <p className="text-sm text-gray-500">
              Portal #{portal?.portal_id ?? "—"}
            </p>
          </div>
        </header>

        {loading && !portal && <Spinner message="Loading portal…" />}

        {error && !portal && (
          <ErrorState error={error} onRetry={() => reload()} />
        )}

        {portal && (
          <PortalContext.Provider value={portal}>
            <div className="mb-6">
              <TabNav tabs={TABS} active={activeTab} onSelect={setActiveTab} />
            </div>
            <main>{renderTab(activeTab)}</main>
          </PortalContext.Provider>
        )}
      </div>
    </div>
  );
}
