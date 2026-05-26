"use client";

import React, { useState, useEffect, useCallback } from "react";
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
  Power,
  GitCompareArrows,
  Percent,
  FlaskConical,
  Layers,
  Gauge
} from "lucide-react";

export default function Home() {
  const [data, setData] = useState<any>(null);
  const [logs, setLogs] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [backendUrl, setBackendUrl] = useState("http://localhost:8000");
  const [pairsData, setPairsData] = useState<any[]>([]);
  const [fundingData, setFundingData] = useState<any>(null);
  const [backtestResult, setBacktestResult] = useState<any>(null);
  const [backtestLoading, setBacktestLoading] = useState(false);
  const [execStatus, setExecStatus] = useState<string>('');
  const [stratDiag, setStratDiag] = useState<any>(null);
  const [btEntryZ, setBtEntryZ] = useState('2.0');
  const [btExitZ, setBtExitZ] = useState('0.5');
  const [btWindow, setBtWindow] = useState('30');
  const [twapForm, setTwapForm] = useState({ coin: 'DOGE', side: 'BUY', size: '', duration: '', slices: '' });
  const [vwapForm, setVwapForm] = useState({ coin: 'DOGE', side: 'BUY', size: '', duration: '', slices: '' });
  const [icebergForm, setIcebergForm] = useState({ coin: 'DOGE', side: 'BUY', totalSize: '', visibleSize: '' });

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

      const resPairs = await fetch(`${backendUrl}/api/scanner/pairs`);
      if (resPairs.ok) {
        const pd = await resPairs.json();
        setPairsData(Array.isArray(pd) ? pd.sort((a: any, b: any) => Math.abs(b.correlation ?? 0) - Math.abs(a.correlation ?? 0)) : []);
      }

      const resFunding = await fetch(`${backendUrl}/api/funding-arbitrage`);
      if (resFunding.ok) {
        setFundingData(await resFunding.json());
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

  // Strategy diagnostics on a 5-second interval
  const fetchStratDiag = useCallback(async () => {
    try {
      const activeCoin = data?.asset_a || 'BTC';
      const res = await fetch(`${backendUrl}/api/strategies/evaluate-all?coin=${activeCoin}`);
      if (res.ok) setStratDiag(await res.json());
    } catch (_) {}
  }, [backendUrl, data?.asset_a]);

  useEffect(() => {
    fetchStratDiag();
    const diagInterval = setInterval(fetchStratDiag, 5000);
    return () => clearInterval(diagInterval);
  }, [fetchStratDiag]);

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

  // Backtest handler
  const handleBacktest = async () => {
    setBacktestLoading(true);
    setBacktestResult(null);
    try {
      const res = await fetch(`${backendUrl}/api/backtest`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ entry_z: parseFloat(btEntryZ), exit_z: parseFloat(btExitZ), window: parseInt(btWindow) })
      });
      if (res.ok) setBacktestResult(await res.json());
    } catch (_) { setBacktestResult({ error: 'Backtest request failed' }); }
    finally { setBacktestLoading(false); }
  };

  // Execution algo handler
  const handleExec = async (endpoint: string, payload: any) => {
    setExecStatus('Submitting...');
    try {
      const res = await fetch(`${backendUrl}${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const result = await res.json();
      setExecStatus(result?.message || result?.status || 'Order submitted');
    } catch (_) { setExecStatus('Execution request failed'); }
  };

  // Funding toggle
  const handleFundingToggle = async () => {
    try {
      await fetch(`${backendUrl}/api/funding-arbitrage/toggle`, { method: 'POST' });
      fetchData();
    } catch (_) {}
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
              <div className="flex justify-between items-center p-2 rounded bg-cyan-500/5 border border-cyan-500/20">
                <span className="text-sm font-bold text-cyan-300">DOGE-PERP</span>
                <span className="text-sm font-semibold text-cyan-400">
                  ${parseFloat(data?.doge_price || "0").toLocaleString(undefined, { minimumFractionDigits: 4 })}
                </span>
              </div>
              <div className="flex justify-between items-center p-2 rounded bg-cyan-500/5 border border-cyan-500/20">
                <span className="text-sm font-bold text-cyan-300">SUI-PERP</span>
                <span className="text-sm font-semibold text-cyan-400">
                  ${parseFloat(data?.sui_price || "0").toLocaleString(undefined, { minimumFractionDigits: 4 })}
                </span>
              </div>
              <div className="flex justify-between items-center p-2 rounded bg-white/5 border border-white/5">
                <span className="text-xs text-zinc-500">BTC-PERP</span>
                <span className="text-xs text-zinc-400">
                  ${parseFloat(data?.btc_price || "0").toLocaleString(undefined, { minimumFractionDigits: 2 })}
                </span>
              </div>
              <div className="flex justify-between items-center p-2 rounded bg-white/5 border border-white/5">
                <span className="text-xs text-zinc-500">ETH-PERP</span>
                <span className="text-xs text-zinc-400">
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
              Z-Score Spread ({data?.asset_a || 'BTC'} / {data?.asset_b || 'ETH'})
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

          {/* ============================================================ */}
          {/* NEW PANEL 1: Altcoin Pairs Scanner Rankings */}
          {/* ============================================================ */}
          <div className="glass-panel rounded-xl p-5">
            <div className="flex justify-between items-center mb-4">
              <h3 className="text-xs font-semibold tracking-wider text-slate-400 uppercase flex items-center gap-1.5">
                <GitCompareArrows className="h-3.5 w-3.5 text-cyan-400" />
                Altcoin Pairs Scanner Rankings
              </h3>
              <span className="text-xs text-zinc-500 font-mono">{pairsData.length} Pairs Tracked</span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left font-mono text-xs">
                <thead>
                  <tr className="border-b border-white/5 text-zinc-500">
                    <th className="pb-2">PAIR</th>
                    <th className="pb-2">CORRELATION</th>
                    <th className="pb-2">HEDGE RATIO</th>
                    <th className="pb-2">STABILITY INDEX</th>
                    <th className="pb-2 text-right">STATUS</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/5">
                  {loading ? (
                    <tr><td colSpan={5} className="py-4 text-center text-zinc-500">Scanning cointegration matrices...</td></tr>
                  ) : pairsData.length === 0 ? (
                    <tr><td colSpan={5} className="py-4 text-center text-zinc-500">No pairs data available. Scanner inactive.</td></tr>
                  ) : (
                    pairsData.map((pair: any, idx: number) => (
                      <tr key={idx} className="hover:bg-white/5">
                        <td className="py-3 font-bold text-white">{pair.pair || `${pair.coin_a}/${pair.coin_b}`}</td>
                        <td className="py-3 text-cyan-300">{(pair.correlation ?? 0).toFixed(4)}</td>
                        <td className="py-3 text-slate-200">{(pair.hedge_ratio ?? 0).toFixed(4)}</td>
                        <td className="py-3 text-slate-200">{(pair.stability_index ?? pair.stability ?? 0).toFixed(4)}</td>
                        <td className="py-3 text-right">
                          <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                            (pair.status || '').toUpperCase() === 'COINTEGRATED'
                              ? 'bg-emerald-950 text-emerald-400 border border-emerald-800/30'
                              : 'bg-zinc-800 text-zinc-400 border border-zinc-700/30'
                          }`}>
                            {(pair.status || 'UNCORRELATED').toUpperCase()}
                          </span>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* ============================================================ */}
          {/* NEW PANEL 2: Funding Arbitrage APY Spreads */}
          {/* ============================================================ */}
          <div className="glass-panel rounded-xl p-5">
            <div className="flex justify-between items-center mb-4">
              <h3 className="text-xs font-semibold tracking-wider text-slate-400 uppercase flex items-center gap-1.5">
                <Percent className="h-3.5 w-3.5 text-cyan-400" />
                Funding Arbitrage APY Spreads
              </h3>
              <button
                onClick={handleFundingToggle}
                className="bg-cyan-500/10 hover:bg-cyan-500/20 text-cyan-400 border border-cyan-500/20 text-xs font-bold px-4 py-2 rounded-lg transition"
              >
                {fundingData?.enabled ? 'Disable' : 'Enable'} Arbitrage
              </button>
            </div>
            {!fundingData || !fundingData.opportunities || fundingData.opportunities.length === 0 ? (
              <p className="text-zinc-500 text-xs">No funding arbitrage opportunities detected. Scanning perpetual markets...</p>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {fundingData.opportunities.map((opp: any, idx: number) => (
                  <div key={idx} className="glass-panel rounded-lg p-4 border border-white/5">
                    <div className="flex justify-between items-start mb-2">
                      <span className="text-sm font-bold text-white font-mono">{opp.coin || opp.symbol}</span>
                      <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                        (opp.status || 'active').toUpperCase() === 'ACTIVE'
                          ? 'bg-emerald-950 text-emerald-400 border border-emerald-800/30'
                          : 'bg-zinc-800 text-zinc-400 border border-zinc-700/30'
                      }`}>
                        {(opp.status || 'ACTIVE').toUpperCase()}
                      </span>
                    </div>
                    <div className="text-xs text-zinc-500 mb-1">8h Funding Rate</div>
                    <div className="text-sm font-mono font-semibold text-slate-200 mb-2">
                      {(opp.funding_rate_8h ?? opp.funding_rate ?? 0).toFixed(6)}%
                    </div>
                    <div className="text-xs text-zinc-500 mb-1">Annualized APY</div>
                    <div className="text-lg font-extrabold font-mono bg-gradient-to-r from-emerald-400 to-cyan-400 bg-clip-text text-transparent">
                      {(opp.annualized_apy ?? opp.apy ?? 0).toFixed(2)}%
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* ============================================================ */}
          {/* NEW PANEL 3: Historical Backtest Simulator */}
          {/* ============================================================ */}
          <div className="glass-panel rounded-xl p-5">
            <h3 className="text-xs font-semibold tracking-wider text-slate-400 mb-4 uppercase flex items-center gap-1.5">
              <FlaskConical className="h-3.5 w-3.5 text-cyan-400" />
              Historical Backtest Simulator
            </h3>
            <div className="grid grid-cols-1 md:grid-cols-4 gap-3 mb-4">
              <div>
                <label className="text-[10px] text-zinc-500 block mb-1">Entry Z-Score Threshold</label>
                <input
                  type="number" step="0.1" value={btEntryZ} onChange={e => setBtEntryZ(e.target.value)}
                  className="w-full bg-zinc-900 border border-white/10 rounded-lg text-xs font-mono text-cyan-300 px-3 py-2 outline-none focus:border-cyan-500/50"
                />
              </div>
              <div>
                <label className="text-[10px] text-zinc-500 block mb-1">Exit Z-Score Threshold</label>
                <input
                  type="number" step="0.1" value={btExitZ} onChange={e => setBtExitZ(e.target.value)}
                  className="w-full bg-zinc-900 border border-white/10 rounded-lg text-xs font-mono text-cyan-300 px-3 py-2 outline-none focus:border-cyan-500/50"
                />
              </div>
              <div>
                <label className="text-[10px] text-zinc-500 block mb-1">Window Size</label>
                <input
                  type="number" step="1" value={btWindow} onChange={e => setBtWindow(e.target.value)}
                  className="w-full bg-zinc-900 border border-white/10 rounded-lg text-xs font-mono text-cyan-300 px-3 py-2 outline-none focus:border-cyan-500/50"
                />
              </div>
              <div className="flex items-end">
                <button
                  onClick={handleBacktest}
                  disabled={backtestLoading}
                  className="w-full bg-cyan-500/10 hover:bg-cyan-500/20 text-cyan-400 border border-cyan-500/20 text-xs font-bold px-4 py-2 rounded-lg transition disabled:opacity-50"
                >
                  {backtestLoading ? 'Simulating...' : 'Run Backtest Simulation'}
                </button>
              </div>
            </div>
            {backtestResult && !backtestResult.error ? (
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
                {[
                  { label: 'Total Trades', value: backtestResult.total_trades ?? '—' },
                  { label: 'Win Rate %', value: backtestResult.win_rate != null ? `${backtestResult.win_rate.toFixed(1)}%` : '—' },
                  { label: 'Sharpe Ratio', value: backtestResult.sharpe_ratio != null ? backtestResult.sharpe_ratio.toFixed(3) : '—' },
                  { label: 'Max Drawdown %', value: backtestResult.max_drawdown != null ? `${backtestResult.max_drawdown.toFixed(2)}%` : '—' },
                  { label: 'Final Balance', value: backtestResult.final_balance != null ? `$${parseFloat(backtestResult.final_balance).toLocaleString(undefined, { minimumFractionDigits: 2 })}` : '—' },
                  { label: 'Total PnL', value: backtestResult.total_pnl != null ? backtestResult.total_pnl : '—', isPnl: true },
                ].map((m, i) => (
                  <div key={i} className="glass-panel rounded-lg p-3 text-center border border-white/5">
                    <div className="text-[10px] text-zinc-500 mb-1">{m.label}</div>
                    <div className={`text-sm font-extrabold font-mono ${
                      m.isPnl ? (parseFloat(String(m.value)) >= 0 ? 'text-emerald-400' : 'text-rose-400') : 'text-white'
                    }`}>
                      {m.isPnl && parseFloat(String(m.value)) > 0 ? '+' : ''}
                      {m.isPnl && m.value !== '—' ? `$${parseFloat(String(m.value)).toLocaleString(undefined, { minimumFractionDigits: 2 })}` : m.value}
                    </div>
                  </div>
                ))}
              </div>
            ) : backtestResult?.error ? (
              <p className="text-rose-400 text-xs font-mono">{backtestResult.error}</p>
            ) : (
              <p className="text-zinc-500 text-xs">Configure parameters and run a backtest to view results.</p>
            )}
          </div>

          {/* ============================================================ */}
          {/* NEW PANEL 4: Execution Algorithms Trigger Panel */}
          {/* ============================================================ */}
          <div className="glass-panel rounded-xl p-5">
            <h3 className="text-xs font-semibold tracking-wider text-slate-400 mb-4 uppercase flex items-center gap-1.5">
              <Layers className="h-3.5 w-3.5 text-cyan-400" />
              Execution Algorithms Trigger Panel
            </h3>
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-4">
              {/* TWAP */}
              <div className="glass-panel rounded-lg p-4 border border-white/5">
                <h4 className="text-[11px] font-bold text-cyan-400 mb-3 tracking-wider">TWAP — Time-Weighted</h4>
                <div className="flex flex-col gap-2">
                  <input placeholder="Coin (e.g. BTC)" value={twapForm.coin} onChange={e => setTwapForm(p => ({ ...p, coin: e.target.value }))}
                    className="bg-zinc-900 border border-white/10 rounded-lg text-xs font-mono text-cyan-300 px-3 py-2 outline-none focus:border-cyan-500/50" />
                  <div className="flex gap-2">
                    <button onClick={() => setTwapForm(p => ({ ...p, side: 'BUY' }))} className={`flex-1 text-[10px] font-bold py-1.5 rounded-lg border transition ${twapForm.side === 'BUY' ? 'bg-emerald-950 text-emerald-400 border-emerald-800/30' : 'bg-zinc-900 text-zinc-500 border-white/10'}`}>BUY</button>
                    <button onClick={() => setTwapForm(p => ({ ...p, side: 'SELL' }))} className={`flex-1 text-[10px] font-bold py-1.5 rounded-lg border transition ${twapForm.side === 'SELL' ? 'bg-rose-950 text-rose-400 border-rose-800/30' : 'bg-zinc-900 text-zinc-500 border-white/10'}`}>SELL</button>
                  </div>
                  <input placeholder="Size" value={twapForm.size} onChange={e => setTwapForm(p => ({ ...p, size: e.target.value }))}
                    className="bg-zinc-900 border border-white/10 rounded-lg text-xs font-mono text-cyan-300 px-3 py-2 outline-none focus:border-cyan-500/50" />
                  <input placeholder="Duration (s)" value={twapForm.duration} onChange={e => setTwapForm(p => ({ ...p, duration: e.target.value }))}
                    className="bg-zinc-900 border border-white/10 rounded-lg text-xs font-mono text-cyan-300 px-3 py-2 outline-none focus:border-cyan-500/50" />
                  <input placeholder="Slices" value={twapForm.slices} onChange={e => setTwapForm(p => ({ ...p, slices: e.target.value }))}
                    className="bg-zinc-900 border border-white/10 rounded-lg text-xs font-mono text-cyan-300 px-3 py-2 outline-none focus:border-cyan-500/50" />
                  <button onClick={() => handleExec('/api/execution/twap', twapForm)}
                    className="bg-cyan-500/10 hover:bg-cyan-500/20 text-cyan-400 border border-cyan-500/20 text-xs font-bold px-4 py-2 rounded-lg transition mt-1">Execute TWAP</button>
                </div>
              </div>
              {/* VWAP */}
              <div className="glass-panel rounded-lg p-4 border border-white/5">
                <h4 className="text-[11px] font-bold text-cyan-400 mb-3 tracking-wider">VWAP — Volume-Weighted</h4>
                <div className="flex flex-col gap-2">
                  <input placeholder="Coin (e.g. BTC)" value={vwapForm.coin} onChange={e => setVwapForm(p => ({ ...p, coin: e.target.value }))}
                    className="bg-zinc-900 border border-white/10 rounded-lg text-xs font-mono text-cyan-300 px-3 py-2 outline-none focus:border-cyan-500/50" />
                  <div className="flex gap-2">
                    <button onClick={() => setVwapForm(p => ({ ...p, side: 'BUY' }))} className={`flex-1 text-[10px] font-bold py-1.5 rounded-lg border transition ${vwapForm.side === 'BUY' ? 'bg-emerald-950 text-emerald-400 border-emerald-800/30' : 'bg-zinc-900 text-zinc-500 border-white/10'}`}>BUY</button>
                    <button onClick={() => setVwapForm(p => ({ ...p, side: 'SELL' }))} className={`flex-1 text-[10px] font-bold py-1.5 rounded-lg border transition ${vwapForm.side === 'SELL' ? 'bg-rose-950 text-rose-400 border-rose-800/30' : 'bg-zinc-900 text-zinc-500 border-white/10'}`}>SELL</button>
                  </div>
                  <input placeholder="Size" value={vwapForm.size} onChange={e => setVwapForm(p => ({ ...p, size: e.target.value }))}
                    className="bg-zinc-900 border border-white/10 rounded-lg text-xs font-mono text-cyan-300 px-3 py-2 outline-none focus:border-cyan-500/50" />
                  <input placeholder="Duration (s)" value={vwapForm.duration} onChange={e => setVwapForm(p => ({ ...p, duration: e.target.value }))}
                    className="bg-zinc-900 border border-white/10 rounded-lg text-xs font-mono text-cyan-300 px-3 py-2 outline-none focus:border-cyan-500/50" />
                  <input placeholder="Slices" value={vwapForm.slices} onChange={e => setVwapForm(p => ({ ...p, slices: e.target.value }))}
                    className="bg-zinc-900 border border-white/10 rounded-lg text-xs font-mono text-cyan-300 px-3 py-2 outline-none focus:border-cyan-500/50" />
                  <button onClick={() => handleExec('/api/execution/vwap', vwapForm)}
                    className="bg-cyan-500/10 hover:bg-cyan-500/20 text-cyan-400 border border-cyan-500/20 text-xs font-bold px-4 py-2 rounded-lg transition mt-1">Execute VWAP</button>
                </div>
              </div>
              {/* Iceberg */}
              <div className="glass-panel rounded-lg p-4 border border-white/5">
                <h4 className="text-[11px] font-bold text-cyan-400 mb-3 tracking-wider">ICEBERG — Hidden Liquidity</h4>
                <div className="flex flex-col gap-2">
                  <input placeholder="Coin (e.g. BTC)" value={icebergForm.coin} onChange={e => setIcebergForm(p => ({ ...p, coin: e.target.value }))}
                    className="bg-zinc-900 border border-white/10 rounded-lg text-xs font-mono text-cyan-300 px-3 py-2 outline-none focus:border-cyan-500/50" />
                  <div className="flex gap-2">
                    <button onClick={() => setIcebergForm(p => ({ ...p, side: 'BUY' }))} className={`flex-1 text-[10px] font-bold py-1.5 rounded-lg border transition ${icebergForm.side === 'BUY' ? 'bg-emerald-950 text-emerald-400 border-emerald-800/30' : 'bg-zinc-900 text-zinc-500 border-white/10'}`}>BUY</button>
                    <button onClick={() => setIcebergForm(p => ({ ...p, side: 'SELL' }))} className={`flex-1 text-[10px] font-bold py-1.5 rounded-lg border transition ${icebergForm.side === 'SELL' ? 'bg-rose-950 text-rose-400 border-rose-800/30' : 'bg-zinc-900 text-zinc-500 border-white/10'}`}>SELL</button>
                  </div>
                  <input placeholder="Total Size" value={icebergForm.totalSize} onChange={e => setIcebergForm(p => ({ ...p, totalSize: e.target.value }))}
                    className="bg-zinc-900 border border-white/10 rounded-lg text-xs font-mono text-cyan-300 px-3 py-2 outline-none focus:border-cyan-500/50" />
                  <input placeholder="Visible Size" value={icebergForm.visibleSize} onChange={e => setIcebergForm(p => ({ ...p, visibleSize: e.target.value }))}
                    className="bg-zinc-900 border border-white/10 rounded-lg text-xs font-mono text-cyan-300 px-3 py-2 outline-none focus:border-cyan-500/50" />
                  <button onClick={() => handleExec('/api/execution/iceberg', icebergForm)}
                    className="bg-cyan-500/10 hover:bg-cyan-500/20 text-cyan-400 border border-cyan-500/20 text-xs font-bold px-4 py-2 rounded-lg transition mt-1">Execute Iceberg</button>
                </div>
              </div>
            </div>
            {execStatus && (
              <div className="bg-zinc-950 border border-white/5 rounded-lg p-3 font-mono text-[10px] text-cyan-400">
                <span className="text-zinc-500">[EXEC]</span> {execStatus}
              </div>
            )}
          </div>

          {/* ============================================================ */}
          {/* NEW PANEL 5: Strategy Diagnostics Monitor */}
          {/* ============================================================ */}
          <div className="glass-panel rounded-xl p-5">
            <h3 className="text-xs font-semibold tracking-wider text-slate-400 mb-4 uppercase flex items-center gap-1.5">
              <Gauge className="h-3.5 w-3.5 text-cyan-400" />
              Strategy Diagnostics Monitor
            </h3>
            {!stratDiag ? (
              <p className="text-zinc-500 text-xs">Awaiting strategy evaluation feed from engine...</p>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {/* Momentum Trend */}
                <div className="glass-panel rounded-lg p-4 border border-white/5">
                  <div className="text-[10px] text-zinc-500 mb-2">Momentum Trend</div>
                  <span className={`inline-block px-3 py-1 rounded text-xs font-bold font-mono ${
                    (stratDiag.momentum?.signal || stratDiag.momentum_trend || 'FLAT').toUpperCase() === 'LONG'
                      ? 'bg-emerald-950 text-emerald-400 border border-emerald-800/30'
                      : (stratDiag.momentum?.signal || stratDiag.momentum_trend || 'FLAT').toUpperCase() === 'SHORT'
                        ? 'bg-rose-950 text-rose-400 border border-rose-800/30'
                        : 'bg-zinc-800 text-zinc-400 border border-zinc-700/30'
                  }`}>
                    {(stratDiag.momentum?.signal || stratDiag.momentum_trend || 'FLAT').toUpperCase()}
                  </span>
                </div>
                {/* Bollinger Breakout */}
                <div className="glass-panel rounded-lg p-4 border border-white/5">
                  <div className="text-[10px] text-zinc-500 mb-2">Bollinger Breakout</div>
                  <span className={`inline-block px-3 py-1 rounded text-xs font-bold font-mono ${
                    (stratDiag.bollinger?.signal || stratDiag.bollinger_breakout || 'FLAT').toUpperCase() === 'LONG'
                      ? 'bg-emerald-950 text-emerald-400 border border-emerald-800/30'
                      : (stratDiag.bollinger?.signal || stratDiag.bollinger_breakout || 'FLAT').toUpperCase() === 'SHORT'
                        ? 'bg-rose-950 text-rose-400 border border-rose-800/30'
                        : 'bg-zinc-800 text-zinc-400 border border-zinc-700/30'
                  }`}>
                    {(stratDiag.bollinger?.signal || stratDiag.bollinger_breakout || 'FLAT').toUpperCase()}
                  </span>
                </div>
                {/* Grid Trading */}
                <div className="glass-panel rounded-lg p-4 border border-white/5">
                  <div className="text-[10px] text-zinc-500 mb-2">Grid Trading Levels</div>
                  <div className="flex gap-4 font-mono text-sm">
                    <div>
                      <span className="text-[10px] text-zinc-500 block">Buy Levels</span>
                      <span className="font-extrabold text-emerald-400">{stratDiag.grid?.buy_levels ?? stratDiag.grid_buy_levels ?? 0}</span>
                    </div>
                    <div>
                      <span className="text-[10px] text-zinc-500 block">Sell Levels</span>
                      <span className="font-extrabold text-rose-400">{stratDiag.grid?.sell_levels ?? stratDiag.grid_sell_levels ?? 0}</span>
                    </div>
                  </div>
                </div>
                {/* Market Making */}
                <div className="glass-panel rounded-lg p-4 border border-white/5">
                  <div className="text-[10px] text-zinc-500 mb-2">Market Making</div>
                  <div className="flex gap-4 font-mono text-sm mb-2">
                    <div>
                      <span className="text-[10px] text-zinc-500 block">Bid</span>
                      <span className="font-extrabold text-emerald-400">${parseFloat(stratDiag.market_making?.bid ?? stratDiag.mm_bid ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}</span>
                    </div>
                    <div>
                      <span className="text-[10px] text-zinc-500 block">Ask</span>
                      <span className="font-extrabold text-rose-400">${parseFloat(stratDiag.market_making?.ask ?? stratDiag.mm_ask ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}</span>
                    </div>
                  </div>
                  {(stratDiag.market_making?.adverse_selection_halt ?? stratDiag.adverse_selection_halt) && (
                    <span className="inline-block px-2 py-0.5 rounded text-[10px] font-bold bg-rose-950 text-rose-400 border border-rose-800/30 animate-pulse">
                      ⚠ ADVERSE SELECTION HALT
                    </span>
                  )}
                </div>
              </div>
            )}
          </div>


        </div>

      </div>
    </main>
  );
}
