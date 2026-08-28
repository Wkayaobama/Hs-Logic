import { useState, type ReactNode } from "react";
import { IconChevronDown } from "./icons";

export interface AccordionItem {
  id: string;
  header: ReactNode;
  body: ReactNode;
}

interface AccordionProps {
  items: AccordionItem[];
}

export default function Accordion({ items }: AccordionProps) {
  const [openIds, setOpenIds] = useState<Set<string>>(new Set());

  const toggle = (id: string) => {
    setOpenIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  return (
    <div className="rounded-xl border border-gray-200 divide-y divide-gray-200 bg-white overflow-hidden">
      {items.map((item) => {
        const isOpen = openIds.has(item.id);
        return (
          <div key={item.id}>
            <button
              type="button"
              onClick={() => toggle(item.id)}
              className="w-full flex items-center justify-between gap-3 px-5 py-3.5 text-left hover:bg-gray-50 transition-colors"
            >
              <div className="flex-1 min-w-0">{item.header}</div>
              <IconChevronDown
                className={`h-4 w-4 flex-shrink-0 text-gray-400 transition-transform ${
                  isOpen ? "rotate-180" : ""
                }`}
              />
            </button>
            {isOpen && (
              <div className="px-5 pb-4 pl-8 bg-gray-50/50">{item.body}</div>
            )}
          </div>
        );
      })}
    </div>
  );
}
