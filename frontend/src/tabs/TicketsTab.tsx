import { usePortal } from "../PortalContext";
import RecordList from "../components/RecordList";
import Badge, { type BadgeTone } from "../components/Badge";
import ExternalLink from "../components/ExternalLink";
import { recordUrl } from "../lib/hubspotLinks";
import { formatDate } from "../lib/format";
import type { TicketRow } from "../api/types";

function priorityTone(priority: string): BadgeTone {
  const value = priority.toUpperCase();
  if (value.includes("HIGH")) return "red";
  if (value.includes("MEDIUM")) return "amber";
  return "gray";
}

export default function TicketsTab() {
  const portal = usePortal();

  return (
    <RecordList<TicketRow>
      path="/tickets"
      rowKey={(row) => row.id}
      header={
        <tr>
          <th className="text-left font-medium px-5 py-3">Subject</th>
          <th className="text-left font-medium px-5 py-3">Priority</th>
          <th className="text-left font-medium px-5 py-3">Stage</th>
          <th className="text-left font-medium px-5 py-3">Created</th>
          <th className="text-left font-medium px-5 py-3">Updated</th>
          <th className="text-right font-medium px-5 py-3"></th>
        </tr>
      }
      renderRow={(row) => (
        <>
          <td className="px-5 py-3 text-gray-900 font-medium">
            {row.subject || "—"}
          </td>
          <td className="px-5 py-3">
            {row.priority ? (
              <Badge tone={priorityTone(row.priority)}>{row.priority}</Badge>
            ) : (
              <span className="text-gray-400">—</span>
            )}
          </td>
          <td className="px-5 py-3 text-gray-600">{row.stage || "—"}</td>
          <td className="px-5 py-3 text-gray-500">
            {formatDate(row.created_at)}
          </td>
          <td className="px-5 py-3 text-gray-500">
            {formatDate(row.updated_at)}
          </td>
          <td className="px-5 py-3 text-right">
            <ExternalLink
              href={recordUrl(portal.portal_id ?? "", "ticket", row.id)}
              title="Open in HubSpot"
            />
          </td>
        </>
      )}
    />
  );
}
