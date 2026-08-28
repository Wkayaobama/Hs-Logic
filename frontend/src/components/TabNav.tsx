import type { ReactNode } from "react";

export interface TabDef {
  id: string;
  label: string;
  icon?: ReactNode;
}

interface TabNavProps {
  tabs: TabDef[];
  active: string;
  onSelect: (id: string) => void;
}

export default function TabNav({ tabs, active, onSelect }: TabNavProps) {
  return (
    <div className="overflow-x-auto -mx-4 px-4 sm:mx-0 sm:px-0">
      <div className="inline-flex items-center gap-1 rounded-full bg-gray-100 p-1 min-w-max">
        {tabs.map((tab) => {
          const isActive = tab.id === active;
          return (
            <button
              key={tab.id}
              type="button"
              onClick={() => onSelect(tab.id)}
              className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-4 py-1.5 text-sm font-medium transition-colors ${
                isActive
                  ? "bg-gray-900 text-white"
                  : "text-gray-600 hover:text-gray-900"
              }`}
            >
              {tab.icon}
              {tab.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
