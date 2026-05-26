"use client";

import React, { useState, useEffect } from "react";
import { 
  Shield, 
  Activity, 
  Terminal, 
  Newspaper, 
  DollarSign, 
  AlertOctagon, 
  RefreshCw, 
  TrendingUp, 
  ArrowUpRight, 
  ArrowDownRight, 
  Zap, 
  Power
} from "lucide-react";

export default function Home() {
  const [data, setData] = useState<any>(null);
  const [logs, setLogs] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [backendUrl, setBackendUrl] = useState("http://localhost:8000");

  // Fetch metrics and logs
  const fetchData = async () => {
    try {
      const resDash = await fetch(`${backendUrl}/api/dashboard`);
      if (resDash.ok) {
        const dashData = await resDash.json();
        setData(dashData);
      }
      
      const resLogs = await fetch(`${backendUrl}/api/logs`);
      if (resLogs.ok) {
        const logsData = await resLogs.json();
        setLogs(logsData);
      }
      
      setLoading(false);
    } catch (err) {
      console.error("Connection to backend failed:", err);
    }
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 2000); // Poll every 2 seconds
    return () => clearInterval(interval);
  }, [backendUrl]);

  // Handle emergency kill switch or restart
  const handleControl = async (action: "HALT" | "RESET") => {
    setActionLoading(true);
    try {
      const res = await fetch(`${backendUrl}/api/emergency-control`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action })
      });
      if (res.ok) {
        await fetchData();
      }
    } catch (err) {
      alert("Failed to communicate control action to backend trading engine.");
    } finally {
      setActionLoading(false);
    }
  };

  // Trigger manual news scrape
  const handleScrape = async () => {
    setActionLoading(true);
    try {
      const res = await fetch(`${backendUrl}/api/scrape-news`, { method: "POST" });
      if (res.ok) {
        await fetchData();
        alert("News scraped successfully and sentiment model updated!");
      }
    } catch (err) {
      alert("Failed to scrape news.");
    } finally {
      setActionLoading(false);
    }
  };

  const getSentimentText = (score: number) => {
    if (score > 0.3) return { text: "BULLISH", color: "text-emerald-400 border-emerald-500/20 bg-emerald-500/5" };
    if (score < -0.3) return { text: "BEARISH", color: "text-rose-400 border-rose-500/20 bg-rose-500/5" };
    return { text: "NEUTRAL", color: "text-zinc-400 border-zinc-500/20 bg-zinc-500/5" };
  };

  return (
    <main className="min-h-screen bg-[#07080D] bg-[radial-gradient(ellipse_80%_80%_at_50%_-20%,rgba(0,240,255,0.07),rgba(255,255,255,0))] px-4 py-6 md:px-8 text-slate-200">
      {/* Top Header */}
      <header className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 mb-8 pb-6 border-b border-white/5">
        <div>
          <div className="flex items-center gap-2">
            <span className="h-2 w-2 rounded-full bg-cyan-400 animate-pulse shadow-[0_0_8px_#22d3ee]" />
            <h1 className="text-xl md:text-2xl font-bold tracking-wider text-white">
              HYPERLIQUID <span className="text-cyan-400">QUANT-DESK</span>
            </h1>
            <span className="text-xs bg-cyan-950 text-cyan-400 px-2 py-0.5 rounded border border-cyan-800/30 uppercase font-mono">
              {data?.is_mock ? "Simulation Mode" : "L1 Authenticated"}
            </span>
          </div>
          <p className="text-xs md:text-sm text-slate-400 mt-1 font-mono">
            Sub-millisecond Statistical Arbitrage & NLP Sentiment Core Engine
          </p>
        </div>

        {/* Global Connection Settings & Actions */}
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center bg-zinc-900 border border-white/5 rounded-lg p-1.5 px-3">
            <span className="text-xs font-mono text-zinc-500 mr-2">Backend:</span>
            <input
              type="text"
              value={backendUrl}
              onChange={(e) => setBackendUrl(e.target.value)}
              className="bg-transparent border-none text-xs font-mono text-cyan-300 w-36 outline-none"
            />
          </div>

          <button
            onClick={handleScrape}
            disabled={actionLoading}
            className="flex items-center gap-1.5 text-xs font-semibold bg-zinc-900 hover:bg-zinc-800 text-slate-200 p-2 px-4 rounded-lg border border-white/10 transition duration-150"
          >
            <RefreshCw className={`h-3 w-3 ${actionLoading ? "animate-spin" : ""}`} />
            Update Sentiment
          </button>

          {data?.is_halted ? (
            <button
              onClick={() => handleControl("RESET")}
              disabled={actionLoading}
              className="flex items-center gap-1.5 text-xs font-bold bg-emerald-500 hover:bg-emerald-600 text-white p-2 px-5 rounded-lg transition duration-150 shadow-[0_0_15px_rgba(16,185,129,0.3)] animate-pulse"
            >
              <Zap className="h-3 w-3" />
              REACTIVATE BOT
            </button>
          ) : (
            <button
              onClick={() => handleControl("HALT")}
              disabled={actionLoading}
              className="flex items-center gap-1.5 text-xs font-bold bg-rose-500 hover:bg-rose-600 text-white p-2 px-5 rounded-lg transition duration-150 shadow-[0_0_15px_rgba(244,63,94,0.3)]"
            >
              <Power className="h-3.5 w-3.5" />
              EMERGENCY STOP (KILL)
            </button>
          )}
        </div>
      </header>

      {/* Main Grid Layout */}
      <div className="grid grid-cols-1 xl:grid-cols-4 gap-6">
        
        {/* Left Column: Stats & Indicators */}
        <div className="xl:col-span-1 flex flex-col gap-6">
          
          {/* Card 1: Balance Summary */}
          <div className="glass-panel rounded-xl p-5 relative overflow-hidden">
            <div className="absolute top-0 right-0 h-24 w-24 bg-cyan-500/5 rounded-full blur-2xl" />
            <div className="flex justify-between items-start mb-4">
              <span className="text-xs font-semibold tracking-wider text-slate-400">MARGIN BALANCE</span>
              <DollarSign className="h-4 w-4 text-cyan-400" />
            </div>
            
            <div className="text-2xl md:text-3xl font-extrabold text-white tracking-tight font-mono">
              ${parseFloat(data?.account_value || "10000.00").toLocaleString(undefined, { minimumFractionDigits: 2 })}
            </div>
            
            <div className="mt-4 pt-4 border-t border-white/5 grid grid-cols-2 gap-2 text-xs">
              <div>
                <span className="text-zinc-500 block">Margin Used</span>
                <span className="font-semibold text-slate-200 font-mono">
                  ${parseFloat(data?.total_margin_used || "0.00").toLocaleString(undefined, { minimumFractionDigits: 2 })}
                </span>
              </div>
              <div>
                <span className="text-zinc-500 block">Withdrawable</span>
                <span className="font-semibold text-slate-200 font-mono">
                  ${parseFloat(data?.withdrawable || "10000.00").toLocaleString(undefined, { minimumFractionDigits: 2 })}
                </span>
              </div>
            </div>
          </div>

          {/* Card 2: Market State Feed */}
          <div className="glass-panel rounded-xl p-5">
            <h3 className="text-xs font-semibold tracking-wider text-slate-400 mb-4 uppercase">Market feeds (L2)</h3>
            <div className="flex flex-col gap-3 font-mono">
              <div className="flex justify-between items-center p-2 rounded bg-white/5 border border-white/5">
                <span className="text-sm font-bold text-white">BTC-PERP</span>
                <span className="text-sm font-semibold text-cyan-400">
                  ${parseFloat(data?.btc_price || "0").toLocaleString(undefined, { minimumFractionDigits: 2 })}
                </span>
              </div>
              <div className="flex justify-between items-center p-2 rounded bg-white/5 border border-white/5">
                <span className="text-sm font-bold text-white">ETH-PERP</span>
                <span className="text-sm font-semibold text-cyan-400">
                  ${parseFloat(data?.eth_price || "0").toLocaleString(undefined, { minimumFractionDigits: 2 })}
                </span>
              </div>
            </div>
          </div>

          {/* Card 3: NLP Sentiment Model */}
          <div className="glass-panel rounded-xl p-5 relative">
            <h3 className="text-xs font-semibold tracking-wider text-slate-400 mb-2 uppercase flex items-center gap-1.5">
              <Newspaper className="h-3.5 w-3.5 text-yellow-500" />
              LLM Sentiment Edge
            </h3>
            
            <div className="my-4 text-center">
              <div className="text-3xl font-extrabold tracking-tight font-mono text-white mb-2">
                {data?.latest_sentiment ? (data.latest_sentiment > 0 ? "+" : "") + data.latest_sentiment.toFixed(2) : "0.00"}
              </div>
              {data && (
                <span className={`inline-block border text-[10px] font-bold px-3 py-1 rounded-full ${getSentimentText(data.latest_sentiment).color}`}>
                  {getSentimentText(data.latest_sentiment).text}
                </span>
              )}
            </div>

            <p className="text-[10px] text-zinc-500 text-center leading-relaxed">
              Z-score skew threshold: {data?.latest_sentiment > 0.3 ? "Skewed Long (-1.5)" : (data?.latest_sentiment < -0.3 ? "Skewed Short (+1.5)" : "Standard Parameters (+/-2.0)")}
            </p>
          </div>

          {/* Card 4: Z-Score Spread Gauge */}
          <div className="glass-panel rounded-xl p-5 flex-grow">
            <h3 className="text-xs font-semibold tracking-wider text-slate-400 mb-4 uppercase flex items-center gap-1.5">
              <Activity className="h-3.5 w-3.5 text-cyan-400" />
              Z-Score Arbitrage Spread
            </h3>
            
            <div className="text-center my-6">
              <div className="text-4xl font-extrabold font-mono text-cyan-300">
                {data?.current_zscore ? data.current_zscore.toFixed(3) : "0.000"}
              </div>
              <span className="text-xs text-zinc-500 font-mono mt-1 block">Spread Diff: {data?.current_spread ? data.current_spread.toFixed(2) : "0.00"}</span>
            </div>

            {/* Visual Z-Score Bar */}
            <div className="relative h-2 bg-zinc-900 rounded-full overflow-hidden border border-white/5 mt-4">
              <div 
                className="absolute top-0 bottom-0 w-1 bg-yellow-400 left-1/2 -ml-0.5 z-10" 
                title="Mean (0)"
              />
              <div 
                className="absolute top-0 bottom-0 w-1 bg-rose-500 left-[15%] -ml-0.5 z-10" 
                title="Long Trigger (-2.0)"
              />
              <div 
                className="absolute top-0 bottom-0 w-1 bg-rose-500 left-[85%] -ml-0.5 z-10" 
                title="Short Trigger (2.0)"
              />
              {data?.current_zscore !== undefined && (
                <div 
                  className="absolute top-0 bottom-0 w-2.5 h-2.5 rounded-full bg-cyan-400 shadow-[0_0_6px_#22d3ee] transition-all duration-300 -translate-y-[1px]" 
                  style={{
                    left: `${Math.max(5, Math.min(95, 50 + (data.current_zscore * 17.5)))}%`
                  }}
                />
              )}
            </div>
            <div className="flex justify-between text-[9px] text-zinc-500 font-mono mt-2 px-1">
              <span>-3.0 (Long)</span>
              <span>0.0 (Mean)</span>
              <span>+3.0 (Short)</span>
            </div>
          </div>

        </div>

        {/* Center/Right Area: Details & Actions */}
        <div className="xl:col-span-3 flex flex-col gap-6">

          {/* Active Positions */}
          <div className="glass-panel rounded-xl p-5">
            <div className="flex justify-between items-center mb-4">
              <h3 className="text-xs font-semibold tracking-wider text-slate-400 uppercase flex items-center gap-1.5">
                <Shield className="h-3.5 w-3.5 text-cyan-400" />
                Active Perpetual Positions
              </h3>
              <span className="text-xs text-zinc-500 font-mono">
                {data?.positions?.length || 0} Positions Open
              </span>
            </div>
            
            <div className="overflow-x-auto">
              <table className="w-full text-left font-mono text-xs">
                <thead>
                  <tr className="border-b border-white/5 text-zinc-500">
                    <th className="pb-2">ASSET</th>
                    <th className="pb-2">SIDE</th>
                    <th className="pb-2">SIZE</th>
                    <th className="pb-2 text-right">ENTRY PRICE</th>
                    <th className="pb-2 text-right">UNREALIZED PNL</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/5">
                  {loading ? (
                    <tr>
                      <td colSpan={5} className="py-4 text-center text-zinc-500">Fetching position matrices from L1...</td>
                    </tr>
                  ) : !data?.positions || data.positions.length === 0 ? (
                    <tr>
                      <td colSpan={5} className="py-4 text-center text-zinc-500">No active positions on Hyperliquid. Bot is scanning spreads...</td>
                    </tr>
                  ) : (
                    data.positions.map((pos: any, idx: number) => (
                      <tr key={idx} className="hover:bg-white/5">
                        <td className="py-3 font-bold text-white">{pos.coin}-PERP</td>
                        <td className="py-3">
                          <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${pos.side === "LONG" ? "bg-emerald-950 text-emerald-400 border border-emerald-800/30" : "bg-rose-950 text-rose-400 border border-rose-800/30"}`}>
                            {pos.side}
                          </span>
                        </td>
                        <td className="py-3">{pos.size}</td>
                        <td className="py-3 text-right">${pos.entry_px.toLocaleString(undefined, { minimumFractionDigits: 2 })}</td>
                        <td className={`py-3 text-right font-bold ${pos.unrealized_pnl >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                          {pos.unrealized_pnl >= 0 ? "+" : ""}${pos.unrealized_pnl.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* Row of Two Columns: news feed and trades */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            
            {/* Column A: NLP Scraped Articles */}
            <div className="glass-panel rounded-xl p-5">
              <h3 className="text-xs font-semibold tracking-wider text-slate-400 mb-4 uppercase flex items-center gap-1.5">
                <Newspaper className="h-3.5 w-3.5 text-yellow-500" />
                Real-time scraped news
              </h3>
              
              <div className="flex flex-col gap-3 max-h-[300px] overflow-y-auto pr-1">
                {loading ? (
                  <p className="text-zinc-500 text-xs">Loading news models...</p>
                ) : !data?.sentiment_logs || data.sentiment_logs.length === 0 ? (
                  <p className="text-zinc-500 text-xs">No scraped news found. Use &apos;Update Sentiment&apos; to trigger scraper.</p>
                ) : (
                  data.sentiment_logs.map((news: any, idx: number) => (
                    <div key={idx} className="p-3 bg-white/5 border border-white/5 rounded-lg flex justify-between gap-3 items-start">
                      <div className="flex-grow">
                        <span className="text-[10px] text-zinc-500 font-mono block">
                          {new Date(news.timestamp).toLocaleTimeString()} | Source: {news.source.includes("coindesk") ? "CoinDesk" : "CoinTelegraph"}
                        </span>
                        <h4 className="text-xs font-bold text-white mt-1 leading-snug">{news.title}</h4>
                        <p className="text-[10px] text-slate-400 mt-1 italic leading-relaxed">{news.summary}</p>
                      </div>
                      <div className="flex-shrink-0">
                        <span className={`text-[10px] font-mono font-extrabold rounded p-1 px-1.5 block text-center ${news.sentiment_score >= 0.25 ? "bg-emerald-950 text-emerald-400" : (news.sentiment_score <= -0.25 ? "bg-rose-950 text-rose-400" : "bg-zinc-800 text-zinc-400")}`}>
                          {(news.sentiment_score > 0 ? "+" : "") + news.sentiment_score.toFixed(2)}
                        </span>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>

            {/* Column B: Recent L1 Trades */}
            <div className="glass-panel rounded-xl p-5">
              <h3 className="text-xs font-semibold tracking-wider text-slate-400 mb-4 uppercase flex items-center gap-1.5">
                <TrendingUp className="h-3.5 w-3.5 text-cyan-400" />
                Recent L1 trades
              </h3>
              
              <div className="flex flex-col gap-2 max-h-[300px] overflow-y-auto pr-1">
                {loading ? (
                  <p className="text-zinc-500 text-xs">Loading execution history...</p>
                ) : !data?.recent_trades || data.recent_trades.length === 0 ? (
                  <p className="text-zinc-500 text-xs">Scanning market for arbitrage signals. No trade records found yet.</p>
                ) : (
                  data.recent_trades.map((trade: any, idx: number) => (
                    <div key={idx} className="flex justify-between items-center p-2.5 bg-white/5 border border-white/5 rounded-lg text-xs font-mono">
                      <div>
                        <div className="flex items-center gap-1.5">
                          <span className={`h-1.5 w-1.5 rounded-full ${trade.side === "BUY" ? "bg-emerald-400" : "bg-rose-400"}`} />
                          <span className="font-bold text-white">{trade.coin}-PERP</span>
                          <span className={`font-bold ${trade.side === "BUY" ? "text-emerald-400" : "text-rose-400"}`}>{trade.side}</span>
                        </div>
                        <span className="text-[10px] text-zinc-500 block mt-0.5">{new Date(trade.timestamp).toLocaleString()}</span>
                      </div>
                      <div className="text-right">
                        <span className="text-slate-200 block">Qty: {trade.size}</span>
                        <span className="text-zinc-400 text-[10px] block">Price: ${trade.price.toLocaleString(undefined, { minimumFractionDigits: 2 })}</span>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>

          </div>

          {/* Engine Terminal Console Logs */}
          <div className="glass-panel rounded-xl p-5 flex-grow">
            <h3 className="text-xs font-semibold tracking-wider text-slate-400 mb-4 uppercase flex items-center gap-1.5">
              <Terminal className="h-3.5 w-3.5 text-cyan-400" />
              Engine Operation Terminal Logs
            </h3>
            
            <div className="bg-zinc-950 border border-white/5 rounded-lg p-4 font-mono text-[10px] leading-relaxed max-h-[220px] overflow-y-auto text-emerald-400/90 select-text">
              {logs.length === 0 ? (
                <div className="text-zinc-500">Establishing API WebSocket link to engine terminal logs...</div>
              ) : (
                logs.map((log: any, idx: number) => (
                  <div key={idx} className="flex gap-2 py-0.5 hover:bg-white/5">
                    <span className="text-zinc-600">[{new Date(log.timestamp).toLocaleTimeString()}]</span>
                    <span className={`font-bold uppercase flex-shrink-0 ${log.level === "ERROR" || log.level === "CRITICAL" ? "text-rose-500" : (log.level === "WARNING" ? "text-yellow-500" : (log.level === "EXECUTION" || log.level === "EMERGENCY" ? "text-cyan-400" : "text-zinc-500"))}`}>
                      {log.level}:
                    </span>
                    <span className="text-slate-300 break-all">{log.message}</span>
                  </div>
                ))
              )}
            </div>
          </div>

        </div>

      </div>
    </main>
  );
}
