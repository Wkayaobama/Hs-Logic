import { usePortal } from "../PortalContext";
import { useApi } from "../hooks/useApi";
import Spinner from "../components/Spinner";
import ErrorState from "../components/ErrorState";
import Banner from "../components/Banner";
import StatCard from "../components/StatCard";
import Badge from "../components/Badge";
import ExternalLink from "../components/ExternalLink";
import Accordion, { type AccordionItem } from "../components/Accordion";
import { IconRefresh, IconShield } from "../components/icons";
import { recordUrl } from "../lib/hubspotLinks";
import { formatNumber, timeAgo } from "../lib/format";
import type { CrmHealth, DuplicateCompanyCluster } from "../api/types";

function orderClusters(
  clusters: DuplicateCompanyCluster[]
): DuplicateCompanyCluster[] {
  return [...clusters].sort((a, b) => {
    if (a.type === b.type) return 0;
    return a.type === "domain" ? -1 : 1;
  });
}

export default function CrmHealthTab() {
  const portal = usePortal();
  const { data, loading, error, reload } = useApi<CrmHealth>("/crm-health");

  if (loading && !data) {
    return (
      <Spinner message="Scanning companies — first run can take a minute or two…" />
    );
  }

  if (error && !data) {
    return <ErrorState error={error} onRetry={() => reload()} />;
  }

  if (!data) return null;

  const orderedClusters = orderClusters(data.duplicate_clusters);

  const accordionItems: AccordionItem[] = orderedClusters.map((cluster) => ({
    id: `${cluster.type}:${cluster.key}`,
    header: (
      <div className="flex items-center gap-2 flex-wrap">
        <Badge tone={cluster.type === "domain" ? "blue" : "gray"}>
          {cluster.type}
        </Badge>
        <span className="font-mono text-sm text-gray-800">{cluster.key}</span>
        <span className="text-xs text-gray-400">
          {formatNumber(cluster.count)} companies
        </span>
      </div>
    ),
    body: (
      <div className="space-y-2">
        {cluster.companies.map((member) => (
          <div
            key={member.id}
            className="flex items-center justify-between gap-3 py-1 text-sm"
          >
            <div className="min-w-0">
              <span className="text-gray-900">{member.name || "—"}</span>
              <span className="text-gray-400 ml-2">{member.domain}</span>
            </div>
            <ExternalLink
              href={recordUrl(portal.portal_id ?? "", "company", member.id)}
              title="Open in HubSpot"
            />
          </div>
        ))}
        {cluster.count > cluster.companies.length && (
          <p className="text-xs text-gray-400 pt-1">
            {formatNumber(cluster.count)} members, showing{" "}
            {formatNumber(cluster.companies.length)}
          </p>
        )}
      </div>
    ),
  }));

  return (
    <div className="space-y-6">
      <Banner icon={<IconShield className="h-5 w-5" />}>
        <span>
          Scanned <strong>{formatNumber(data.scanned_companies)}</strong>{" "}
          companies · {formatNumber(data.fc_deal_count)} FC deals ·{" "}
          {formatNumber(data.edge_company_count)} edge-excluded
          {data.capped && (
            <span className="font-semibold text-amber-700">
              {" "}
              · cap reached — results partial
            </span>
          )}
        </span>
        <span className="flex items-center gap-3 text-xs text-gray-500">
          {data.cache && <span>Last scanned {timeAgo(data.cache.age_seconds)}</span>}
          <button
            type="button"
            onClick={() => reload(true)}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50 transition-colors"
          >
            <IconRefresh className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </button>
        </span>
      </Banner>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-xs text-red-700">
          Refresh failed — showing the previous scan. ({error.detail})
        </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label="Orphan companies"
          value={formatNumber(data.orphan_count)}
          tone={data.orphan_count > 0 ? "red" : "neutral"}
        />
        <StatCard
          label="Duplicate clusters"
          value={formatNumber(data.duplicate_cluster_count)}
          tone={data.duplicate_cluster_count > 0 ? "amber" : "neutral"}
        />
        <StatCard
          label="Companies scanned"
          value={formatNumber(data.scanned_companies)}
        />
        <StatCard
          label="FC deals found"
          value={formatNumber(data.fc_deal_count)}
        />
      </div>

      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        <div className="flex items-center gap-2 px-5 py-4 border-b border-gray-100">
          <h2 className="text-sm font-semibold text-gray-900">
            Orphan companies — no deals, not edge-linked, not an FC name
          </h2>
          <Badge tone="red">{formatNumber(data.orphan_count)}</Badge>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-xs uppercase text-gray-500">
              <tr>
                <th className="text-left font-medium px-5 py-3">Name</th>
                <th className="text-left font-medium px-5 py-3">Domain</th>
                <th className="text-right font-medium px-5 py-3"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {data.orphans.map((company) => (
                <tr key={company.id} className="hover:bg-gray-50">
                  <td className="px-5 py-3 text-gray-900 font-medium">
                    {company.name || "—"}
                  </td>
                  <td className="px-5 py-3 text-gray-500">
                    {company.domain || "—"}
                  </td>
                  <td className="px-5 py-3 text-right">
                    <ExternalLink
                      href={recordUrl(portal.portal_id ?? "", "company", company.id)}
                      title="Open in HubSpot"
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.orphans.length === 0 && (
            <p className="text-center text-sm text-gray-500 py-10">
              No orphan companies found.
            </p>
          )}
        </div>
        {data.orphan_count > 150 && (
          <div className="px-5 py-3 border-t border-gray-100 text-xs text-gray-400">
            showing first 150 of {formatNumber(data.orphan_count)}
          </div>
        )}
      </div>

      <div>
        <div className="flex items-center gap-2 mb-3">
          <h2 className="text-sm font-semibold text-gray-900">
            Duplicate clusters
          </h2>
          <Badge tone="amber">{formatNumber(data.duplicate_cluster_count)}</Badge>
        </div>
        {accordionItems.length > 0 ? (
          <Accordion items={accordionItems} />
        ) : (
          <div className="bg-white rounded-xl border border-gray-200 p-8 text-center text-sm text-gray-500">
            No duplicate clusters found.
          </div>
        )}
        {data.duplicate_cluster_count > orderedClusters.length && (
          <p className="text-xs text-gray-400 mt-2">
            showing first {formatNumber(orderedClusters.length)} of{" "}
            {formatNumber(data.duplicate_cluster_count)} clusters
          </p>
        )}
      </div>
    </div>
  );
}
