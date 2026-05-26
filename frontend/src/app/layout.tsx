import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Hyperliquid LLM Trading Engine",
  description: "High-Frequency Statistical Arbitrage & NLP Sentiment Quantitative Desk",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased min-h-screen bg-[#090A0F] text-slate-100 selection:bg-cyan-500/30 selection:text-cyan-300">
        {children}
      </body>
    </html>
  );
}
