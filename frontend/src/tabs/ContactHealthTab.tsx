import { useState } from "react";
import { usePortal } from "../PortalContext";
import { useApi } from "../hooks/useApi";
import { apiPost, ApiError } from "../api/client";
import Spinner from "../components/Spinner";
import ErrorState from "../components/ErrorState";
import Banner from "../components/Banner";
import StatCard from "../components/StatCard";
import Badge from "../components/Badge";
import ExternalLink from "../components/ExternalLink";
import Accordion, { type AccordionItem } from "../components/Accordion";
import {
  IconCopy,
  IconLink,
  IconPersonCheck,
  IconPersonX,
  IconRefresh,
  IconShield,
  IconWarning,
} from "../components/icons";
import { recordUrl } from "../lib/hubspotLinks";
import { formatNumber, timeAgo } from "../lib/format";
import { downloadCsv } from "../lib/csv";
import type {
  ContactHealth,
  DuplicateContactCluster,
  HealthContactRow,
  MultiCompanyContact,
  SuppressionResult,
} from "../api/types";

function orderContactClusters(
  clusters: DuplicateContactCluster[]
): DuplicateContactCluster[] {
  return [...clusters].sort((a, b) => {
    if (a.type === b.type) return 0;
    return a.type === "email" ? -1 : 1;
  });
}

function barColor(pct: number): string {
  if (pct <= 20) return "bg-green-500";
  if (pct <= 40) return "bg-amber-500";
  return "bg-rose-500";
}

function MissingChips({ missing }: { missing?: string[] }) {
  if (!missing || missing.length === 0) return <span className="text-gray-300">—</span>;
  return (
    <div className="flex flex-wrap gap-1">
      {missing.map((field) => (
        <span
          key={field}
          className="inline-flex items-center rounded-full bg-red-50 text-red-600 px-1.5 py-0.5 text-[10px] font-medium"
        >
          {field}
        </span>
      ))}
    </div>
  );
}

interface SuppressionButtonProps {
  nqlIds: string[];
  nqlContacts: HealthContactRow[];
}

function SuppressionButton({ nqlIds, nqlContacts }: SuppressionButtonProps) {
  const [posting, setPosting] = useState(false);
  const [result, setResult] = useState<SuppressionResult | null>(null);
  const [csvFallback, setCsvFallback] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const handleClick = () => {
    setPosting(true);
    setErrorMessage(null);
    setCsvFallback(false);
    setResult(null);

    apiPost<SuppressionResult>("/suppression-list", {
      contact_ids: nqlIds,
      list_name: "",
    })
      .then((res) => {
        setResult(res);
        setPosting(false);
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 403) {
          // Export every NQL id (the uncapped list), joining row detail from
          // the 150-row detail slice where available.
          const detailById = new Map(nqlContacts.map((c) => [c.id, c]));
          downloadCsv(
            "nql-suppression.csv",
            nqlIds.map((id) => {
              const c = detailById.get(id);
              return {
                id,
                name: c?.name ?? "",
                email: c?.email ?? "",
                phone: c?.phone ?? "",
                lifecycle: c?.lifecycle ?? "",
              };
            })
          );
          setCsvFallback(true);
        } else {
          setErrorMessage(
            err instanceof ApiError ? err.detail : String(err)
          );
        }
        setPosting(false);
      });
  };

  return (
    <div className="flex flex-col items-end gap-2">
      <button
        type="button"
        onClick={handleClick}
        disabled={posting || nqlIds.length === 0}
        className="inline-flex items-center rounded-lg bg-gray-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-gray-800 disabled:opacity-50 transition-colors"
      >
        {posting
          ? "Working…"
          : `Export suppression list (${formatNumber(nqlIds.length)})`}
      </button>

      {result && (
        <div
          className={`w-full max-w-sm rounded-lg border px-3 py-2 text-xs ${
            result.errors > 0
              ? "bg-amber-50 border-amber-200 text-amber-800"
              : "bg-green-50 border-green-200 text-green-800"
          }`}
        >
          Added {formatNumber(result.added)} contacts to &lsquo;{result.list_name}&rsquo;.
          {result.errors > 0 && (
            <span className="font-semibold">
              {" "}
              {formatNumber(result.errors)} failed to add — the list is
              incomplete.
            </span>
          )}{" "}
          <a
            href={result.url}
            target="_blank"
            rel="noopener noreferrer"
            className="font-medium underline"
          >
            Open list in HubSpot ↗
          </a>
        </div>
      )}

      {csvFallback && (
        <div className="w-full max-w-sm rounded-lg bg-amber-50 border border-amber-200 px-3 py-2 text-xs text-amber-800">
          Lists scope missing — downloaded CSV instead (all{" "}
          {formatNumber(nqlIds.length)} IDs; name/email detail available for the
          first 150)
        </div>
      )}

      {errorMessage && (
        <div className="w-full max-w-sm rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-700">
          {errorMessage}
        </div>
      )}
    </div>
  );
}

export default function ContactHealthTab() {
  const portal = usePortal();
  const { data, loading, error, reload } = useApi<ContactHealth>("/contact-health");

  if (loading && !data) {
    return (
      <Spinner message="Scanning contacts — first run can take a minute or two…" />
    );
  }

  if (error && !data) {
    return <ErrorState error={error} onRetry={() => reload()} />;
  }

  if (!data) return null;

  const missingFields: Array<[keyof ContactHealth["missing_prevalence"], string]> = [
    ["email", "Email"],
    ["phone", "Phone"],
    ["name", "Name"],
    ["company", "Company"],
  ];

  const clusterItems: AccordionItem[] = orderContactClusters(
    data.duplicate_clusters
  ).map((cluster) => ({
    id: `${cluster.type}:${cluster.key}`,
    header: (
      <div className="flex items-center gap-2 flex-wrap">
        <Badge tone={cluster.type === "email" ? "blue" : "gray"}>
          {cluster.type}
        </Badge>
        <span className="font-mono text-sm text-gray-800">{cluster.key}</span>
        <span className="text-xs text-gray-400">
          {formatNumber(cluster.count)} contacts
        </span>
      </div>
    ),
    body: (
      <div className="space-y-2">
        {cluster.contacts.map((member) => (
          <div
            key={member.id}
            className="flex items-center justify-between gap-3 py-1 text-sm"
          >
            <div className="min-w-0 flex items-center gap-2 flex-wrap">
              <span className="text-gray-900">{member.name || "—"}</span>
              <span className="text-gray-400">{member.email}</span>
              {member.lifecycle && <Badge tone="blue">{member.lifecycle}</Badge>}
            </div>
            <ExternalLink
              href={recordUrl(portal.portal_id ?? "", "contact", member.id)}
              title="Open in HubSpot"
            />
          </div>
        ))}
        {cluster.count > cluster.contacts.length && (
          <p className="text-xs text-gray-400 pt-1">
            {formatNumber(cluster.count)} members, showing{" "}
            {formatNumber(cluster.contacts.length)}
          </p>
        )}
      </div>
    ),
  }));

  return (
    <div className="space-y-6">
      <Banner icon={<IconShield className="h-5 w-5" />}>
        <span>
          Scanned <strong>{formatNumber(data.scanned_contacts)}</strong> contacts
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
          label="NQL contacts"
          value={formatNumber(data.nql_count)}
          tone={data.nql_count > 0 ? "red" : "neutral"}
          icon={<IconPersonX className="h-5 w-5" />}
        />
        <StatCard
          label="_MQL contacts"
          value={formatNumber(data.mql_count)}
          tone="green"
          icon={<IconPersonCheck className="h-5 w-5" />}
        />
        <StatCard
          label="Duplicate clusters"
          value={formatNumber(data.duplicate_cluster_count)}
          tone={data.duplicate_cluster_count > 0 ? "amber" : "neutral"}
          icon={<IconCopy className="h-5 w-5" />}
        />
        <StatCard
          label="Multi-company"
          value={formatNumber(data.multi_company_count)}
          tone={data.multi_company_count > 0 ? "orange" : "neutral"}
          icon={<IconLink className="h-5 w-5" />}
        />
      </div>

      <div className="bg-white rounded-xl border border-gray-200 p-5">
        <div className="flex items-center gap-2 mb-1">
          <IconWarning className="h-4 w-4 text-amber-500" />
          <h2 className="text-sm font-semibold text-gray-900">
            Missing field prevalence — across all {formatNumber(data.scanned_contacts)}{" "}
            contacts
          </h2>
        </div>
        <p className="text-xs text-gray-500 mb-4">
          How often each key field is absent in the full contact database
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5">
          {missingFields.map(([key, label]) => {
            const stat = data.missing_prevalence[key];
            return (
              <div key={key}>
                <div className="flex items-center justify-between text-sm mb-1.5">
                  <span className="text-gray-700 font-medium">{label}</span>
                  <span className="text-gray-500">{stat.pct}% missing</span>
                </div>
                <div className="h-1.5 rounded-full bg-gray-100 overflow-hidden">
                  <div
                    className={`h-full rounded-full ${barColor(stat.pct)}`}
                    style={{ width: `${Math.min(100, Math.max(0, stat.pct))}%` }}
                  />
                </div>
                <p className="text-xs text-gray-400 mt-1">
                  {formatNumber(stat.count)} contacts
                </p>
              </div>
            );
          })}
        </div>
      </div>

      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        <div className="flex items-center justify-between gap-3 px-5 py-4 border-b border-gray-100 flex-wrap">
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-semibold text-gray-900">
                NQL — Not Qualified for CRM
              </h2>
              <Badge tone="red">{formatNumber(data.nql_count)}</Badge>
            </div>
            <p className="text-xs text-gray-500 mt-1">
              Unreachable (no email + no phone) or anonymous (no name) — too sparse
              to be actionable
            </p>
          </div>
          <SuppressionButton nqlIds={data.nql_ids} nqlContacts={data.nql_contacts} />
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-xs uppercase text-gray-500">
              <tr>
                <th className="text-left font-medium px-5 py-3">Name</th>
                <th className="text-left font-medium px-5 py-3">Email</th>
                <th className="text-left font-medium px-5 py-3">Phone</th>
                <th className="text-left font-medium px-5 py-3">Missing</th>
                <th className="text-left font-medium px-5 py-3">Lifecycle</th>
                <th className="text-right font-medium px-5 py-3"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {data.nql_contacts.map((row) => (
                <tr key={row.id} className="hover:bg-gray-50">
                  <td className="px-5 py-3 text-gray-900 font-medium">
                    {row.name || "—"}
                  </td>
                  <td className="px-5 py-3 text-gray-600">{row.email || "—"}</td>
                  <td className="px-5 py-3 text-gray-600">{row.phone || "—"}</td>
                  <td className="px-5 py-3">
                    <MissingChips missing={row.missing} />
                  </td>
                  <td className="px-5 py-3">
                    {row.lifecycle ? (
                      <Badge tone="blue">{row.lifecycle}</Badge>
                    ) : (
                      <span className="text-gray-400">—</span>
                    )}
                  </td>
                  <td className="px-5 py-3 text-right">
                    <ExternalLink
                      href={recordUrl(portal.portal_id ?? "", "contact", row.id)}
                      title="Open in HubSpot"
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.nql_contacts.length === 0 && (
            <p className="text-center text-sm text-gray-500 py-10">
              No NQL contacts found.
            </p>
          )}
        </div>
        {data.nql_count > 150 && (
          <div className="px-5 py-3 border-t border-gray-100 text-xs text-gray-400">
            showing first 150 of {formatNumber(data.nql_count)}
          </div>
        )}
      </div>

      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        <div className="px-5 py-4 border-b border-gray-100">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold text-gray-900">
              _MQL — meets minimum bar
            </h2>
            <Badge tone="green">{formatNumber(data.mql_count)}</Badge>
          </div>
          <p className="text-xs text-gray-500 mt-1">
            Email + name + company association, or already lifecycle-qualified
          </p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-xs uppercase text-gray-500">
              <tr>
                <th className="text-left font-medium px-5 py-3">Name</th>
                <th className="text-left font-medium px-5 py-3">Email</th>
                <th className="text-left font-medium px-5 py-3">Lifecycle</th>
                <th className="text-left font-medium px-5 py-3">Companies</th>
                <th className="text-right font-medium px-5 py-3"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {data.mql_contacts.map((row) => (
                <tr key={row.id} className="hover:bg-gray-50">
                  <td className="px-5 py-3 text-gray-900 font-medium">
                    {row.name || "—"}
                  </td>
                  <td className="px-5 py-3 text-gray-600">{row.email || "—"}</td>
                  <td className="px-5 py-3">
                    {row.lifecycle ? (
                      <Badge tone="blue">{row.lifecycle}</Badge>
                    ) : (
                      <span className="text-gray-400">—</span>
                    )}
                  </td>
                  <td className="px-5 py-3 text-gray-600">
                    {formatNumber(row.company_count)}
                  </td>
                  <td className="px-5 py-3 text-right">
                    <ExternalLink
                      href={recordUrl(portal.portal_id ?? "", "contact", row.id)}
                      title="Open in HubSpot"
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.mql_contacts.length === 0 && (
            <p className="text-center text-sm text-gray-500 py-10">
              No _MQL contacts found.
            </p>
          )}
        </div>
        <div className="px-5 py-3 border-t border-gray-100 text-xs text-gray-400">
          showing {formatNumber(data.mql_contacts.length)} of{" "}
          {formatNumber(data.mql_count)}
        </div>
      </div>

      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        <div className="px-5 py-4 border-b border-gray-100">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold text-gray-900">Borderline</h2>
            <Badge tone="amber">{formatNumber(data.borderline_count)}</Badge>
          </div>
          <p className="text-xs text-gray-500 mt-1">
            Reachable but incomplete — needs enrichment before qualifying
          </p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-xs uppercase text-gray-500">
              <tr>
                <th className="text-left font-medium px-5 py-3">Name</th>
                <th className="text-left font-medium px-5 py-3">Email</th>
                <th className="text-left font-medium px-5 py-3">Lifecycle</th>
                <th className="text-left font-medium px-5 py-3">Companies</th>
                <th className="text-left font-medium px-5 py-3">Missing</th>
                <th className="text-right font-medium px-5 py-3"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {data.borderline_contacts.map((row) => (
                <tr key={row.id} className="hover:bg-gray-50">
                  <td className="px-5 py-3 text-gray-900 font-medium">
                    {row.name || "—"}
                  </td>
                  <td className="px-5 py-3 text-gray-600">{row.email || "—"}</td>
                  <td className="px-5 py-3">
                    {row.lifecycle ? (
                      <Badge tone="blue">{row.lifecycle}</Badge>
                    ) : (
                      <span className="text-gray-400">—</span>
                    )}
                  </td>
                  <td className="px-5 py-3 text-gray-600">
                    {formatNumber(row.company_count)}
                  </td>
                  <td className="px-5 py-3">
                    <MissingChips missing={row.missing} />
                  </td>
                  <td className="px-5 py-3 text-right">
                    <ExternalLink
                      href={recordUrl(portal.portal_id ?? "", "contact", row.id)}
                      title="Open in HubSpot"
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.borderline_contacts.length === 0 && (
            <p className="text-center text-sm text-gray-500 py-10">
              No borderline contacts found.
            </p>
          )}
        </div>
        {data.borderline_count > data.borderline_contacts.length && (
          <div className="px-5 py-3 border-t border-gray-100 text-xs text-gray-400">
            showing {formatNumber(data.borderline_contacts.length)} of{" "}
            {formatNumber(data.borderline_count)}
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
        {clusterItems.length > 0 ? (
          <Accordion items={clusterItems} />
        ) : (
          <div className="bg-white rounded-xl border border-gray-200 p-8 text-center text-sm text-gray-500">
            No duplicate clusters found.
          </div>
        )}
        {data.duplicate_cluster_count > clusterItems.length && (
          <p className="text-xs text-gray-400 mt-2">
            showing first {formatNumber(clusterItems.length)} of{" "}
            {formatNumber(data.duplicate_cluster_count)} clusters
          </p>
        )}
      </div>

      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        <div className="px-5 py-4 border-b border-gray-100">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold text-gray-900">
              Multi-company contacts
            </h2>
            <Badge tone="amber">{formatNumber(data.multi_company_count)}</Badge>
          </div>
          <p className="text-xs text-gray-500 mt-1">
            Associated with 2+ companies — violates single-company cardinality
          </p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-xs uppercase text-gray-500">
              <tr>
                <th className="text-left font-medium px-5 py-3">Name</th>
                <th className="text-left font-medium px-5 py-3">Email</th>
                <th className="text-left font-medium px-5 py-3">Companies</th>
                <th className="text-right font-medium px-5 py-3"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {data.multi_company_contacts.map((row: MultiCompanyContact) => (
                <tr key={row.id} className="hover:bg-gray-50">
                  <td className="px-5 py-3 text-gray-900 font-medium">
                    {row.name || "—"}
                  </td>
                  <td className="px-5 py-3 text-gray-600">{row.email || "—"}</td>
                  <td className="px-5 py-3 text-gray-600">
                    {formatNumber(row.company_count)}
                  </td>
                  <td className="px-5 py-3 text-right">
                    <ExternalLink
                      href={recordUrl(portal.portal_id ?? "", "contact", row.id)}
                      title="Open in HubSpot"
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.multi_company_contacts.length === 0 && (
            <p className="text-center text-sm text-gray-500 py-10">
              No multi-company contacts found.
            </p>
          )}
        </div>
        {data.multi_company_count > data.multi_company_contacts.length && (
          <div className="px-5 py-3 border-t border-gray-100 text-xs text-gray-400">
            {formatNumber(data.multi_company_count)} total, showing{" "}
            {formatNumber(data.multi_company_contacts.length)}
          </div>
        )}
      </div>
    </div>
  );
}
