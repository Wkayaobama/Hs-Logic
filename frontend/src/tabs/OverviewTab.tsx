import { usePortal } from "../PortalContext";
import StatCard from "../components/StatCard";
import { formatDate, formatNumber } from "../lib/format";
import {
  IconBuilding,
  IconDeal,
  IconPerson,
  IconTicket,
} from "../components/icons";

function countLabel(n: number): string {
  return n < 0 ? "—" : formatNumber(n);
}

export default function OverviewTab() {
  const portal = usePortal();
  const { counts } = portal;

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label="Contacts"
          value={countLabel(counts.contacts)}
          icon={<IconPerson className="h-5 w-5" />}
        />
        <StatCard
          label="Companies"
          value={countLabel(counts.companies)}
          icon={<IconBuilding className="h-5 w-5" />}
        />
        <StatCard
          label="Deals"
          value={countLabel(counts.deals)}
          icon={<IconDeal className="h-5 w-5" />}
        />
        <StatCard
          label="Tickets"
          value={countLabel(counts.tickets)}
          icon={<IconTicket className="h-5 w-5" />}
        />
      </div>

      <div className="bg-white rounded-xl border border-gray-200 p-5">
        <h2 className="text-sm font-semibold text-gray-900 mb-4">
          Portal details
        </h2>
        <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-4">
          <div>
            <dt className="text-xs uppercase tracking-wide text-gray-400">
              Time zone
            </dt>
            <dd className="text-sm text-gray-900 mt-1">
              {portal.timezone || "—"}
            </dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide text-gray-400">
              Currency
            </dt>
            <dd className="text-sm text-gray-900 mt-1">
              {portal.currency || "—"}
            </dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide text-gray-400">
              Hub domain
            </dt>
            <dd className="text-sm text-gray-900 mt-1">
              {portal.hub_domain ? (
                <a
                  href={`https://${portal.hub_domain}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-blue-600 hover:underline"
                >
                  {portal.hub_domain}
                </a>
              ) : (
                "—"
              )}
            </dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide text-gray-400">
              Created
            </dt>
            <dd className="text-sm text-gray-900 mt-1">
              {formatDate(portal.created_at)}
            </dd>
          </div>
        </dl>
      </div>
    </div>
  );
}
