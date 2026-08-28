import type { ReactNode } from "react";

interface BannerProps {
  icon?: ReactNode;
  children: ReactNode;
  tone?: "neutral" | "amber";
}

export default function Banner({ icon, children, tone = "neutral" }: BannerProps) {
  const toneClasses =
    tone === "amber"
      ? "bg-amber-50 border-amber-200 text-amber-900"
      : "bg-white border-gray-200 text-gray-700";

  return (
    <div
      className={`w-full rounded-lg border p-4 flex items-center gap-3 ${toneClasses}`}
    >
      {icon && (
        <span
          className={`flex-shrink-0 ${
            tone === "amber" ? "text-amber-500" : "text-gray-400"
          }`}
        >
          {icon}
        </span>
      )}
      <div className="flex-1 flex flex-wrap items-center justify-between gap-3 text-sm">
        {children}
      </div>
    </div>
  );
}
