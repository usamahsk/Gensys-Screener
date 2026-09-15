"use client";

import { useState } from "react";
import { Menu, X } from "lucide-react";
import { BrandLogo } from "@/components/brand-logo";
import { SideNav } from "@/components/side-nav";

/** Mobile top bar + slide-over nav. The sidebar is desktop-only. */
export function MobileNav() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <div className="sticky top-0 z-30 -mx-4 -mt-4 mb-4 flex items-center justify-between border-b border-[#E2E8F4] bg-white/90 px-4 py-3 backdrop-blur md:hidden">
        <BrandLogo />
        <button
          type="button"
          aria-label="Open menu"
          onClick={() => setOpen(true)}
          className="flex h-9 w-9 items-center justify-center rounded-xl bg-[#EAF1FF] text-[#0068FF]"
        >
          <Menu className="h-5 w-5" />
        </button>
      </div>
      {open && (
        <div className="fixed inset-0 z-50 md:hidden">
          <div
            className="absolute inset-0 bg-black/40 backdrop-blur-[2px]"
            onClick={() => setOpen(false)}
          />
          <div className="absolute inset-y-0 left-0 flex w-72 max-w-[85vw] flex-col bg-[#000E49] shadow-xl">
            <div className="flex items-center justify-between bg-white px-4 py-3">
              <BrandLogo />
              <button
                type="button"
                aria-label="Close menu"
                onClick={() => setOpen(false)}
                className="flex h-8 w-8 items-center justify-center rounded-lg bg-[#EAF1FF] text-[#0068FF]"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto">
              <SideNav onNavigate={() => setOpen(false)} />
            </div>
          </div>
        </div>
      )}
    </>
  );
}
