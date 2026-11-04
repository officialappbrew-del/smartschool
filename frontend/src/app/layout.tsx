import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Northstar School | School Management",
  description: "A connected workspace for your school community.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
