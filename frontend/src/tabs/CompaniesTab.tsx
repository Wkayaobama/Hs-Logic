import { usePortal } from "../PortalContext";
import RecordList from "../components/RecordList";
import ExternalLink from "../components/ExternalLink";
import { recordUrl } from "../lib/hubspotLinks";
import { formatDate, formatMoney } from "../lib/format";
import type { CompanyRow } from "../api/types";

export default function CompaniesTab() {
  const portal = usePortal();

  return (
    <RecordList<CompanyRow>
      path="/companies"
      rowKey={(row) => row.id}
      header={
        <tr>
          <th className="text-left font-medium px-5 py-3">Name</th>
          <th className="text-left font-medium px-5 py-3">Domain</th>
          <th className="text-left font-medium px-5 py-3">Industry</th>
          <th className="text-left font-medium px-5 py-3">Employees</th>
          <th className="text-left font-medium px-5 py-3">Revenue</th>
          <th className="text-left font-medium px-5 py-3">Created</th>
          <th className="text-right font-medium px-5 py-3"></th>
        </tr>
      }
      renderRow={(row) => (
        <>
          <td className="px-5 py-3 text-gray-900 font-medium">
            {row.name || "—"}
          </td>
          <td className="px-5 py-3">
            {row.domain ? (
              <a
                href={`https://${row.domain}`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-gray-500 hover:text-blue-600 hover:underline"
              >
                {row.domain}
              </a>
            ) : (
              <span className="text-gray-400">—</span>
            )}
          </td>
          <td className="px-5 py-3 text-gray-600">{row.industry || "—"}</td>
          <td className="px-5 py-3 text-gray-600">{row.employees || "—"}</td>
          <td className="px-5 py-3 text-gray-600">
            {formatMoney(row.revenue, portal.currency)}
          </td>
          <td className="px-5 py-3 text-gray-500">
            {formatDate(row.created_at)}
          </td>
          <td className="px-5 py-3 text-right">
            <ExternalLink
              href={recordUrl(portal.portal_id ?? "", "company", row.id)}
              title="Open in HubSpot"
            />
          </td>
        </>
      )}
    />
  );
}
