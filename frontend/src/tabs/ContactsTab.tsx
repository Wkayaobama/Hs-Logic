import { usePortal } from "../PortalContext";
import RecordList from "../components/RecordList";
import Badge from "../components/Badge";
import ExternalLink from "../components/ExternalLink";
import { recordUrl } from "../lib/hubspotLinks";
import { formatDate } from "../lib/format";
import type { ContactRow } from "../api/types";

function initials(firstname: string, lastname: string): string {
  const a = firstname?.trim()?.[0] ?? "";
  const b = lastname?.trim()?.[0] ?? "";
  const combined = `${a}${b}`.toUpperCase();
  return combined || "?";
}

export default function ContactsTab() {
  const portal = usePortal();

  return (
    <RecordList<ContactRow>
      path="/contacts"
      rowKey={(row) => row.id}
      header={
        <tr>
          <th className="text-left font-medium px-5 py-3">Name</th>
          <th className="text-left font-medium px-5 py-3">Email</th>
          <th className="text-left font-medium px-5 py-3">Company</th>
          <th className="text-left font-medium px-5 py-3">Lifecycle</th>
          <th className="text-left font-medium px-5 py-3">Created</th>
          <th className="text-right font-medium px-5 py-3"></th>
        </tr>
      }
      renderRow={(row) => {
        const name =
          [row.firstname, row.lastname].filter(Boolean).join(" ") || "—";
        return (
          <>
            <td className="px-5 py-3">
              <div className="flex items-center gap-3">
                <div className="h-8 w-8 rounded-full bg-gray-200 text-gray-600 flex items-center justify-center text-xs font-semibold flex-shrink-0">
                  {initials(row.firstname, row.lastname)}
                </div>
                <span className="text-gray-900 font-medium">{name}</span>
              </div>
            </td>
            <td className="px-5 py-3 text-gray-600">{row.email || "—"}</td>
            <td className="px-5 py-3 text-gray-600">{row.company || "—"}</td>
            <td className="px-5 py-3">
              {row.lifecycle ? (
                <Badge tone="blue">{row.lifecycle}</Badge>
              ) : (
                <span className="text-gray-400">—</span>
              )}
            </td>
            <td className="px-5 py-3 text-gray-500">
              {formatDate(row.created_at)}
            </td>
            <td className="px-5 py-3 text-right">
              <ExternalLink
                href={recordUrl(portal.portal_id ?? "", "contact", row.id)}
                title="Open in HubSpot"
              />
            </td>
          </>
        );
      }}
    />
  );
}
