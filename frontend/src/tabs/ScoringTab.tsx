import { usePortal } from "../PortalContext";
import { useApi } from "../hooks/useApi";
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
import type { ContactScoresResponse, ScoredContactRow } from "../api/types";

const BAND_TONES: Record<string, BadgeTone> = {
  hot: "green",
  warm: "amber",
  cold: "gray",
};

function BandBadge({ band }: { band: string }) {
  return <Badge tone={BAND_TONES[band] ?? "gray"}>{band}</Badge>;
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

  return (
    <div className="space-y-6">
      <Banner icon={<IconShield className="h-5 w-5" />}>
        <span>
          Scored <strong>{formatNumber(data.scanned)}</strong> contacts against{" "}
          <strong>{data.criteria.length}</strong> static criteria
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
          label="Hot"
          value={formatNumber(data.band_counts.hot ?? 0)}
          tone="green"
          icon={<IconPersonCheck className="h-5 w-5" />}
        />
        <StatCard
          label="Warm"
          value={formatNumber(data.band_counts.warm ?? 0)}
          tone="amber"
          icon={<IconWarning className="h-5 w-5" />}
        />
        <StatCard
          label="Cold"
          value={formatNumber(data.band_counts.cold ?? 0)}
          tone="neutral"
          icon={<IconPersonX className="h-5 w-5" />}
        />
      </div>

      <div className="bg-white rounded-xl border border-gray-200 p-5">
        <h2 className="text-sm font-semibold text-gray-900 mb-1">
          Scoring criteria
        </h2>
        <p className="text-xs text-gray-500 mb-4">
          Static, declarative rules — edit backend/app/scoring/criteria.py to
          change the model
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {data.criteria.map((rule) => (
            <div
              key={rule.id}
              className="flex items-center justify-between gap-3 rounded-lg border border-gray-100 bg-gray-50 px-3 py-2 text-sm"
            >
              <span className="text-gray-700">{rule.label}</span>
              <Badge tone="blue">+{rule.points}</Badge>
            </div>
          ))}
        </div>
      </div>

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
