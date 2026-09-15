"use client";

import { useState } from "react";
import { Headset } from "lucide-react";

/**
 * VEngage brand lockup — the provided icon + wordmark artwork, shown as-is.
 */
export function BrandLogo({ compact = false }: { compact?: boolean }) {
  const [iconFailed, setIconFailed] = useState(false);
  const [wordFailed, setWordFailed] = useState(false);

  return (
    <div className="flex items-center gap-3">
      {iconFailed ? (
        <span className="flex h-12 w-12 shrink-0 items-center justify-center bg-[#0068FF]">
          <Headset className="h-6 w-6 text-white" strokeWidth={2.25} />
        </span>
      ) : (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src="/vengage-icon.png"
          alt="VEngage icon"
          className="h-12 w-12 shrink-0"
          onError={() => setIconFailed(true)}
        />
      )}
      {!compact && (
        <div className="leading-tight">
          {wordFailed ? (
            <div className="text-2xl font-extrabold tracking-tight text-[#000E49]">
              VEngage
            </div>
          ) : (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src="/vengage-wordmark.png"
              alt="VEngage"
              className="h-8 w-auto"
              onError={() => setWordFailed(true)}
            />
          )}
          <div className="mt-1 text-[10px] font-medium tracking-wide text-[#000E49]/60">
            by D Square Technologies
          </div>
        </div>
      )}
    </div>
  );
}
