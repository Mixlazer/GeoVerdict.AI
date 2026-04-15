import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "GeoVerdict.AI",
  description: "Умный помощник по выбору места для магазина"
};

export default function RootLayout({
  children
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
