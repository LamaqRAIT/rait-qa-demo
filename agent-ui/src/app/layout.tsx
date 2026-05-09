import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "RAIT QA Agent",
  description: "Live QA pipeline dashboard",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" data-theme="dark">
      <body className="min-h-screen bg-canvas text-cream font-sans">
        {children}
      </body>
    </html>
  );
}
