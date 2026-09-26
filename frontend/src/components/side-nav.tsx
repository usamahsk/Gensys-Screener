"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  ClipboardList,
  Headset,
  LayoutDashboard,
  Settings,
  Cpu,
  BookOpen,
  FileSpreadsheet,
  Phone,
  BarChart3,
  Blocks,
} from "lucide-react";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/transactions", label: "Transactions", icon: ClipboardList },
  { href: "/telephony", label: "Telephony", icon: Phone },
  { href: "/analytics", label: "Analytics", icon: BarChart3 },
  { href: "/knowledge-base", label: "Knowledge Base", icon: BookOpen },
  { href: "/reports", label: "Reports", icon: FileSpreadsheet },
  { href: "/integrations", label: "Integrations", icon: Blocks },
  { href: "/agent", label: "Agent View", icon: Headset },
  { href: "/models", label: "Models (BYOK)", icon: Cpu },
  { href: "/settings", label: "Settings", icon: Settings },
];

function isActive(pathname: string, href: string) {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function SideNav({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  return (
    <nav className="flex flex-col gap-1.5 p-3">
      {NAV.map(({ href, label, icon: Icon }) => {
        const active = isActive(pathname, href);
        return (
          <Link
            key={href}
            href={href}
            onClick={onNavigate}
            aria-current={active ? "page" : undefined}
            className={cn(
              "group relative flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition-all",
              active
                ? "bg-[#0068FF] text-white shadow"
                : "text-white/70 hover:bg-white/10 hover:text-white"
            )}
          >
            <span
              className={cn(
                "flex h-8 w-8 shrink-0 items-center justify-center rounded-lg transition-colors",
                active
                  ? "bg-white/20"
                  : "bg-white/10 group-hover:bg-[#0068FF]"
              )}
            >
              <Icon className="h-4 w-4" />
            </span>
            {label}
            {active && (
              <span className="absolute top-1/2 -right-[13px] h-8 w-1 -translate-y-1/2 rounded-l-full bg-white shadow" />
            )}
          </Link>
        );
      })}
    </nav>
  );
}
