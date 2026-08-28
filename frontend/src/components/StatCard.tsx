import type { ReactNode } from "react";

export type StatTone = "neutral" | "red" | "amber" | "green" | "orange";

interface StatCardProps {
  label: string;
  value: string | number;
  tone?: StatTone;
  icon?: ReactNode;
}

const toneClasses: Record<StatTone, { chip: string; value: string }> = {
  neutral: { chip: "bg-gray-100 text-gray-500", value: "text-gray-900" },
  red: { chip: "bg-red-50 text-red-600", value: "text-red-600" },
  amber: { chip: "bg-amber-50 text-amber-600", value: "text-amber-600" },
  green: { chip: "bg-green-50 text-green-600", value: "text-green-600" },
  orange: { chip: "bg-orange-50 text-orange-600", value: "text-orange-600" },
};

export default function StatCard({
  label,
  value,
  tone = "neutral",
  icon,
}: StatCardProps) {
  const classes = toneClasses[tone];

  return (
    <div className="bg-white rounded-xl border border-gray-200 p-5">
      {icon && (
        <div
          className={`inline-flex items-center justify-center h-9 w-9 rounded-lg mb-3 ${classes.chip}`}
        >
          {icon}
        </div>
      )}
      <div className={`text-3xl font-bold ${classes.value}`}>{value}</div>
      <div className="text-sm text-gray-500 mt-1">{label}</div>
    </div>
  );
}
