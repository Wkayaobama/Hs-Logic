import { useEffect, useState } from "react";
import { usePortal } from "../PortalContext";
import RecordList from "../components/RecordList";
import ExternalLink from "../components/ExternalLink";
import { recordUrl } from "../lib/hubspotLinks";
import { apiGet } from "../api/client";
import { formatDate, formatMoney } from "../lib/format";
import type { DealRow, PipelinesResponse } from "../api/types";

function formatProbability(raw: string): string {
  if (!raw) return "—";
  const value = parseFloat(raw);
  if (Number.isNaN(value)) return "—";
  const pct = value <= 1 ? value * 100 : value;
  return `${Math.round(pct)}%`;
}

export default function DealsTab() {
  const portal = usePortal();
  const [stageLabels, setStageLabels] = useState<Record<string, string>>({});

  useEffect(() => {
    let cancelled = false;
    apiGet<PipelinesResponse>("/pipelines")
      .then((res) => {
        if (cancelled) return;
        const map: Record<string, string> = {};
        for (const pipeline of res.pipelines) {
          for (const stage of pipeline.stages) {
            map[stage.id] = stage.label;
          }
        }
        setStageLabels(map);
      })
      .catch(() => {
        // Stage labels are a display nicety; fall back to raw ids silently.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <RecordList<DealRow>
      path="/deals"
      rowKey={(row) => row.id}
      header={
        <tr>
          <th className="text-left font-medium px-5 py-3">Name</th>
          <th className="text-left font-medium px-5 py-3">Amount</th>
          <th className="text-left font-medium px-5 py-3">Stage</th>
          <th className="text-left font-medium px-5 py-3">Close date</th>
          <th className="text-left font-medium px-5 py-3">Probability</th>
          <th className="text-left font-medium px-5 py-3">Created</th>
          <th className="text-right font-medium px-5 py-3"></th>
        </tr>
      }
      renderRow={(row) => (
        <>
          <td className="px-5 py-3 text-gray-900 font-medium">
            {row.name || "—"}
          </td>
          <td className="px-5 py-3 font-semibold text-gray-900">
            {formatMoney(row.amount, portal.currency)}
          </td>
          <td className="px-5 py-3 text-gray-600">
            {stageLabels[row.stage] || row.stage || "—"}
          </td>
          <td className="px-5 py-3 text-gray-500">
            {formatDate(row.close_date)}
          </td>
          <td className="px-5 py-3 text-gray-600">
            {formatProbability(row.probability)}
          </td>
          <td className="px-5 py-3 text-gray-500">
            {formatDate(row.created_at)}
          </td>
          <td className="px-5 py-3 text-right">
            <ExternalLink
              href={recordUrl(portal.portal_id ?? "", "deal", row.id)}
              title="Open in HubSpot"
            />
          </td>
        </>
      )}
    />
  );
}
