import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "UrbanTrafficLab",
  description: "Road-network editing, traffic simulation, and scenario comparison.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
