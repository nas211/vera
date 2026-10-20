import type { Metadata, Viewport } from "next";
import { Bricolage_Grotesque, Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { HelpDesk } from "@/components/help/HelpDesk";

const bricolage = Bricolage_Grotesque({
  subsets: ["latin"],
  axes: ["opsz"],
  variable: "--font-display",
  display: "swap",
});

const inter = Inter({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-body",
  display: "swap",
});

const jetbrains = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "700"],
  variable: "--font-mono",
  display: "swap",
});

export const viewport: Viewport = {
  themeColor: "#F0852A",
};

export const metadata: Metadata = {
  title: "WHY TRADE — Alpha, manufactured.",
  description:
    "AI-powered crypto trading — fully automated. Scans 2,000+ trading setups, finds long and short opportunities, filters every trade through strict risk rules and executes approved trades through your own connected exchange.",
  metadataBase: new URL("https://whytradingbot.com"),
  openGraph: {
    title:       "WHY TRADE — Alpha, manufactured.",
    description: "Autonomous crypto trading engine. 340+ live strategies. 99.98% uptime. Plug in — let it print.",
    url:         "https://whytradingbot.com",
    siteName:    "WHY TRADE",
    locale:      "en_US",
    type:        "website",
  },
  twitter: {
    card:        "summary_large_image",
    title:       "WHY TRADE — Alpha, manufactured.",
    description: "Autonomous crypto trading engine. 340+ live strategies. 99.98% uptime. Plug in — let it print.",
  },
  robots: {
    index:  true,
    follow: true,
  },
  keywords: [
    "crypto trading bot", "autonomous trading", "algorithmic trading",
    "WHY TRADE", "alpha engine", "crypto alpha", "trading engine",
    "DeFi", "yield",
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${bricolage.variable} ${inter.variable} ${jetbrains.variable}`}>
      <body>{children}<HelpDesk /></body>
    </html>
  );
}
