import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { Toaster } from "@/components/ui/sonner";
import { BrandLogo } from "@/components/brand-logo";
import { SideNav } from "@/components/side-nav";
import { MobileNav } from "@/components/mobile-nav";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "VEngage Voice Commerce",
  description: "VEngage by D Square Technologies — voice AI ordering operations dashboard",
  icons: { icon: "/vengage-icon.png" },
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={`${geistSans.variable} ${geistMono.variable} antialiased`}>
        <div className="flex h-screen overflow-hidden">
          {/* Fixed sidebar — never scrolls with the page */}
          <aside className="sticky top-0 hidden h-screen w-64 shrink-0 flex-col bg-[#000E49] shadow-xl md:flex">
            <div className="shrink-0 border-b border-[#E2E8F4] bg-white px-5 py-4">
              <BrandLogo />
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto">
              <SideNav />
            </div>
            <div className="shrink-0 space-y-1 px-5 py-4">
              <div className="rounded-xl bg-white/10 p-3 text-xs leading-relaxed text-white/80">
                <span className="font-semibold text-white">VEngage</span> voice commerce
                <br />
                <span className="text-white/60">Orders · Calls · Runs</span>
              </div>
              <div className="px-1 pt-1 text-[11px] text-white/50">
                POC · single-user · Docker
              </div>
            </div>
          </aside>
          {/* Only this pane scrolls */}
          <main className="relative h-screen min-w-0 flex-1 overflow-y-auto p-4 md:p-6">
            {/* Barely-visible blurred logo watermark (content area only) */}
            <div
              aria-hidden
              className="pointer-events-none fixed inset-y-0 right-0 left-0 z-0 flex items-center justify-center md:left-64"
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src="/v-engage-logo.jpeg"
                alt=""
                draggable={false}
                className="h-[540px] w-[540px] object-contain opacity-[0.05] blur-[3px] select-none"
              />
            </div>
            <div className="page-in relative z-10 mx-auto max-w-6xl pb-10">
              <MobileNav />
              {children}
            </div>
          </main>
        </div>
        <Toaster richColors />
      </body>
    </html>
  );
}
