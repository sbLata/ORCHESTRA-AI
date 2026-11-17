import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ORCHESTRA AI",
  description: "One objective. A coordinated AI workforce. A completed outcome.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
