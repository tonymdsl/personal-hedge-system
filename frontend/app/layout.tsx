import type { Metadata } from "next";

import "./globals.css";
import { Sidebar } from "@/components/layout/sidebar";
import { Topbar } from "@/components/layout/topbar";

export const metadata: Metadata = {
  title: "Personal Hedge System",
  description: "Local market analytics platform"
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="dark">
      <body>
        <Sidebar />
        <div className="min-h-screen lg:pl-64">
          <Topbar />
          <main className="mx-auto w-full max-w-7xl px-5 py-6 lg:px-8">{children}</main>
        </div>
      </body>
    </html>
  );
}
