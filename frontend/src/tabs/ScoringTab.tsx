import { useState } from "react";
import { usePortal } from "../PortalContext";
import { useApi } from "../hooks/useApi";
import { apiGet, ApiError } from "../api/client";
import Spinner from "../components/Spinner";
import ErrorState from "../components/ErrorState";
import Banner from "../components/Banner";
import StatCard from "../components/StatCard";
import Badge, { type BadgeTone } from "../components/Badge";
import ExternalLink from "../components/ExternalLink";
import {
  IconPersonCheck,
  IconPersonX,
  IconRefresh,
  IconShield,
  IconWarning,
} from "../components/icons";
import { recordUrl } from "../lib/hubspotLinks";
import { formatNumber, timeAgo } from "../lib/format";
import type {
  ContactScoresResponse,
  ProbeEntry,
  ProbeResponse,
  ScoredContactRow,
} from "../api/types";

const BAND_TONES: Record<string, BadgeTone> = {
  hot: "green",
  warm: "amber",
  cold: "gray",
};

const CLASS_TONES: Record<string, BadgeTone> = {
  mql: "green",
  borderline: "amber",
  nql: "red",
};

function BandBadge({ band }: { band: string }) {
  return <Badge tone={BAND_TONES[band] ?? "gray"}>{band}</Badge>;
}

function ClassBadge({ classification }: { classification: string }) {
  return (
    <Badge tone={CLASS_TONES[classification] ?? "gray"}>{classification}</Badge>
  );
}

function UnmetChips({ row }: { row: ScoredContactRow }) {
  const unmet = row.criteria.filter((c) => !c.met);
  if (unmet.length === 0) return <span className="text-gray-300">—</span>;
  return (
    <div className="flex flex-wrap gap-1">
      {unmet.map((c) => (
        <span
          key={c.id}
          className="inline-flex items-center rounded-full bg-red-50 text-red-600 px-1.5 py-0.5 text-[10px] font-medium"
          title={`${c.label} (+${c.max_points})`}
        >
          {c.label}
        </span>
      ))}
    </div>
  );
}

interface ScoreTableProps {
  title: string;
  subtitle: string;
  rows: ScoredContactRow[];
  maxScore: number;
  portalId: number | string;
}

function ScoreTable({ title, subtitle, rows, maxScore, portalId }: ScoreTableProps) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
      <div className="px-5 py-4 border-b border-gray-100">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-gray-900">{title}</h2>
          <Badge tone="gray">{formatNumber(rows.length)}</Badge>
        </div>
        <p className="text-xs text-gray-500 mt-1">{subtitle}</p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs uppercase text-gray-500">
            <tr>
              <th className="text-left font-medium px-5 py-3">Name</th>
              <th className="text-left font-medium px-5 py-3">Email</th>
              <th className="text-left font-medium px-5 py-3">Score</th>
              <th className="text-left font-medium px-5 py-3">Band</th>
              <th className="text-left font-medium px-5 py-3">Class</th>
              <th className="text-left font-medium px-5 py-3">Unmet criteria</th>
              <th className="text-right font-medium px-5 py-3"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {rows.map((row) => (
              <tr key={row.id} className="hover:bg-gray-50">
                <td className="px-5 py-3 text-gray-900 font-medium">
                  {row.name || "—"}
                </td>
                <td className="px-5 py-3 text-gray-600">{row.email || "—"}</td>
                <td className="px-5 py-3 text-gray-900">
                  <span className="font-semibold">{row.score}</span>
                  <span className="text-gray-400 text-xs"> / {maxScore}</span>
                </td>
                <td className="px-5 py-3">
                  <BandBadge band={row.band} />
                </td>
                <td className="px-5 py-3">
                  <ClassBadge classification={row.classification} />
                </td>
                <td className="px-5 py-3">
                  <UnmetChips row={row} />
                </td>
                <td className="px-5 py-3 text-right">
                  <ExternalLink
                    href={recordUrl(portalId, "contact", row.id)}
                    title="Open in HubSpot"
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && (
          <p className="text-center text-sm text-gray-500 py-10">
            No contacts scanned.
          </p>
        )}
      </div>
    </div>
  );
}

function ProbeList({ title, entries }: { title: string; entries: ProbeEntry[] }) {
  return (
    <div className="rounded-lg border border-gray-100 bg-gray-50 p-3">
      <h3 className="text-xs font-semibold text-gray-700 mb-2">{title}</h3>
      {entries.length === 0 ? (
        <p className="text-xs text-gray-400">no values observed</p>
      ) : (
        <ul className="space-y-1">
          {entries.slice(0, 10).map((e) => (
            <li key={e.value} className="flex justify-between gap-2 text-xs">
              <span className="text-gray-700 truncate" title={e.value}>
                {e.value}
              </span>
              <span className="text-gray-400 tabular-nums">
                {formatNumber(e.count)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ProbeCard() {
  const [probe, setProbe] = useState<ProbeResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = (refresh: boolean) => {
    setLoading(true);
    setError(null);
    apiGet<ProbeResponse>(`/api/scoring/probe${refresh ? "?refresh=true" : ""}`)
      .then((res) => {
        setProbe(res);
        setLoading(false);
      })
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.detail : String(err));
        setLoading(false);
      });
  };

  return (
    <div className="bg-white rounded-xl border border-gray-200 p-5">
      <div className="flex items-center justify-between gap-3 flex-wrap mb-1">
        <h2 className="text-sm font-semibold text-gray-900">
          Fit-registry probe
        </h2>
        <button
          type="button"
          onClick={() => run(probe !== null)}
          disabled={loading}
          className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50 transition-colors"
        >
          <IconRefresh className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
          {probe ? "Re-run probe" : "Run probe"}
        </button>
      </div>
      <p className="text-xs text-gray-500 mb-4">
        Scans the portal's observed job titles, countries, sources, email
        domains, URL paths and industries — pick the target lists from these,
        then configure them in backend/app/scoring/criteria.py
      </p>

      {error && (
        <div className="rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-700 mb-3">
          {error}
        </div>
      )}
      {loading && !probe && <Spinner message="Probing — full portal scan…" />}

      {probe && (
        <>
          <p className="text-xs text-gray-400 mb-3">
            {formatNumber(probe.scanned_contacts)} contacts /{" "}
            {formatNumber(probe.scanned_companies)} companies scanned · email
            domains: {formatNumber(probe.email_domain_split.corporate)} corporate,{" "}
            {formatNumber(probe.email_domain_split.freemail)} freemail,{" "}
            {formatNumber(probe.email_domain_split.unknown)} unknown
            {(probe.contacts_capped || probe.companies_capped) && (
              <span className="font-semibold text-amber-700"> · cap reached</span>
            )}
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            <ProbeList title="Job-title tokens (→ role fit)" entries={probe.top.job_title_tokens} />
            <ProbeList title="Countries (→ country fit)" entries={probe.top.countries} />
            <ProbeList title="Traffic sources (→ source quality)" entries={probe.top.sources} />
            <ProbeList title="URL paths (→ key pages)" entries={probe.top.url_paths} />
            <ProbeList title="Company industries (→ industry fit)" entries={probe.top.industries} />
            <ProbeList title="Email domains" entries={probe.top.email_domains} />
          </div>
        </>
      )}
    </div>
  );
}

export default function ScoringTab() {
  const portal = usePortal();
  const { data, loading, error, reload } = useApi<ContactScoresResponse>(
    "/api/scoring/contacts"
  );

  if (loading && !data) {
    return (
      <Spinner message="Scoring contacts — first run can take a minute or two…" />
    );
  }

  if (error && !data) {
    return <ErrorState error={error} onRetry={() => reload()} />;
  }

  if (!data) return null;

  const portalId = portal.portal_id ?? "";
  const configured = data.criteria.filter((r) => r.configured);
  const pending = data.criteria.filter((r) => !r.configured);

  return (
    <div className="space-y-6">
      <Banner icon={<IconShield className="h-5 w-5" />}>
        <span>
          Scored <strong>{formatNumber(data.scanned)}</strong> contacts —{" "}
          {formatNumber(configured.length)} active criteria worth{" "}
          <strong>{data.max_score}</strong> points
          {pending.length > 0 && (
            <span className="text-gray-500">
              {" "}
              ({data.full_model_score} once {formatNumber(pending.length)} pending
              registries are configured)
            </span>
          )}
          {data.capped && (
            <span className="font-semibold text-amber-700">
              {" "}
              · cap reached — results partial
            </span>
          )}
        </span>
        <span className="flex items-center gap-3 text-xs text-gray-500">
          {data.cache && <span>Last scored {timeAgo(data.cache.age_seconds)}</span>}
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
          label={`Average score (of ${data.max_score})`}
          value={data.average_score}
          tone="neutral"
          icon={<IconShield className="h-5 w-5" />}
        />
        <StatCard
          label={`MQL (score ≥ ${data.mql_threshold})`}
          value={formatNumber(data.classification_counts.mql ?? 0)}
          tone="green"
          icon={<IconPersonCheck className="h-5 w-5" />}
        />
        <StatCard
          label="Borderline"
          value={formatNumber(data.classification_counts.borderline ?? 0)}
          tone="amber"
          icon={<IconWarning className="h-5 w-5" />}
        />
        <StatCard
          label="NQL (gated out)"
          value={formatNumber(data.classification_counts.nql ?? 0)}
          tone="red"
          icon={<IconPersonX className="h-5 w-5" />}
        />
      </div>

      <div className="bg-white rounded-xl border border-gray-200 p-5">
        <h2 className="text-sm font-semibold text-gray-900 mb-1">
          Scoring criteria
        </h2>
        <p className="text-xs text-gray-500 mb-4">
          Static, declarative rules — edit backend/app/scoring/criteria.py to
          change the model. Bands:{" "}
          {data.bands
            .map((b, i) =>
              i < data.bands.length - 1 ? `${b.label} ≥ ${b.min_points}` : b.label
            )
            .join(" · ")}
          . Gated (NQL): {data.gates.map((g) => g.label.toLowerCase()).join("; ")} —
          whatever the points.
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {data.criteria.map((rule) => (
            <div
              key={rule.id}
              className={`flex items-center justify-between gap-3 rounded-lg border px-3 py-2 text-sm ${
                rule.configured
                  ? "border-gray-100 bg-gray-50"
                  : "border-dashed border-gray-200 bg-white"
              }`}
            >
              <span className={rule.configured ? "text-gray-700" : "text-gray-400"}>
                {rule.label}
              </span>
              {rule.configured ? (
                <Badge tone="blue">+{rule.points}</Badge>
              ) : (
                <Badge tone="gray">pending probe · +{rule.points}</Badge>
              )}
            </div>
          ))}
        </div>
      </div>

      <ProbeCard />

      <ScoreTable
        title="Lowest scores"
        subtitle="The enrichment worklist — contacts furthest from the qualification bar"
        rows={data.lowest}
        maxScore={data.max_score}
        portalId={portalId}
      />

      <ScoreTable
        title="Highest scores"
        subtitle="Sanity check — contacts the criteria rank as most qualified"
        rows={data.highest}
        maxScore={data.max_score}
        portalId={portalId}
      />
    </div>
  );
}
