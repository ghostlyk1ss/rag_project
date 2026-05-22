"use client";

import { useState, useEffect, useRef } from "react";
import {
  fetchMetrics,
  resetCostHistory,
  updateBudget,
  type MetricsData,
  type CacheLayer,
} from "@/lib/api";
import {
  BarChart3,
  Database,
  DollarSign,
  TrendingUp,
  AlertTriangle,
  X,
  RefreshCw,
  Loader2,
  Trash2,
  Settings2,
  Check,
  Cpu,
  Activity,
} from "lucide-react";

interface MetricsPanelProps {
  onClose?: () => void;
}

function fmtRate(v: number): string {
  return `${(v * 100).toFixed(1)}%`;
}

function fmtCost(v: number | undefined | null): string {
  if (v == null || isNaN(v)) return "¥0";
  if (v < 0.001) return `¥${(v * 1000).toFixed(2)}m`;
  return `¥${v.toFixed(4)}`;
}

function fmtTokens(v: number | undefined | null): string {
  if (v == null || isNaN(v as number)) return "0";
  if (v >= 1000000) return `${(v / 1000000).toFixed(1)}M`;
  if (v >= 1000) return `${(v / 1000).toFixed(1)}K`;
  return `${v}`;
}

const LAYER_COLORS: Record<string, { bg: string; dot: string; label: string }> = {
  embedding: { bg: "bg-blue-50 border-blue-200", dot: "bg-blue-500", label: "向量编码" },
  llm: { bg: "bg-violet-50 border-violet-200", dot: "bg-violet-500", label: "LLM 调用" },
  bm25: { bg: "bg-amber-50 border-amber-200", dot: "bg-amber-500", label: "全文检索" },
  query: { bg: "bg-emerald-50 border-emerald-200", dot: "bg-emerald-500", label: "精确查询" },
};

export default function MetricsPanel({ onClose }: MetricsPanelProps) {
  const [data, setData] = useState<MetricsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [editBudget, setEditBudget] = useState(false);
  const [editForm, setEditForm] = useState({ daily: 50, all_time: 200, degrade: "cache_only" });
  const intervalRef = useRef<ReturnType<typeof setInterval>>();

  const load = async () => {
    try {
      const d = await fetchMetrics();
      setData(d);
      if (d.budget) {
        setEditForm({
          daily: d.budget.daily_budget,
          all_time: d.budget.all_time_budget,
          degrade: d.budget.degrade_strategy,
        });
      }
    } catch {
      // retry
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    intervalRef.current = setInterval(load, 10000);
    return () => clearInterval(intervalRef.current);
  }, []);

  if (!data && loading) {
    return (
      <div className="glass rounded-2xl shadow-glass p-4 w-80 flex items-center justify-center gap-2 text-sm text-neutral-400 animate-fade-in">
        <Loader2 className="w-4 h-4 animate-spin" />
        加载中...
      </div>
    );
  }

  if (!data) return null;

  const totalCacheHits = data.caches.reduce((s, c) => s + c.hits, 0);
  const totalCacheMisses = data.caches.reduce((s, c) => s + c.misses, 0);
  const totalReqs = totalCacheHits + totalCacheMisses;
  const overallHitRate = totalReqs > 0 ? totalCacheHits / totalReqs : 0;

  const budgetRatio = data.budget?.daily_ratio || 0;
  const budgetWarning = budgetRatio >= 0.8;
  const budgetDanger = budgetRatio >= 0.95;
  const allTimeRatio = data.budget?.all_time_ratio || 0;
  const allTimeWarning = allTimeRatio >= 0.8;
  const allTimeDanger = allTimeRatio >= 0.95;

  const dailyAlert =
    budgetDanger ? "⚠️ 今日预算即将用尽！" :
    budgetWarning ? "⚠️ 今日预算使用已超80%" : null;
  const allTimeAlert =
    allTimeDanger ? "⚠️ 累计预算即将用尽！" :
    allTimeWarning ? "⚠️ 累计预算使用已超80%" : null;

  return (
    <div className="glass rounded-2xl shadow-glass-lg w-[22rem] max-h-[75vh] overflow-y-auto animate-slide-up">
      {/* Header */}
      <div className="flex items-center justify-between px-3.5 py-2.5 border-b border-neutral-200/60">
        <div className="flex items-center gap-1.5 text-sm font-medium text-neutral-900">
          <Activity className="w-4 h-4 text-primary-500" />
          <span>系统状态</span>
        </div>
        <div className="flex items-center gap-1">
          <button onClick={load} className="p-1 hover:bg-neutral-100 rounded-lg transition-colors" title="刷新">
            <RefreshCw className="w-3.5 h-3.5 text-neutral-400" />
          </button>
          {onClose && (
            <button onClick={onClose} className="p-1 hover:bg-neutral-100 rounded-lg transition-colors" title="关闭">
              <X className="w-3.5 h-3.5 text-neutral-400" />
            </button>
          )}
        </div>
      </div>

      <div className="p-3.5 space-y-3 text-xs">
        {/* Cache hit rate */}
        <div>
          <div className="flex items-center gap-1.5 text-neutral-700 font-medium mb-2">
            <Database className="w-3.5 h-3.5 text-neutral-400" />
            <span className="text-neutral-500">缓存命中率</span>
            <span className="ml-auto font-mono text-neutral-400">{fmtRate(overallHitRate)}</span>
          </div>
          <div className="space-y-1.5">
            {data.caches.map((c: CacheLayer) => {
              const style = LAYER_COLORS[c.layer] || { bg: "bg-neutral-50 border-neutral-200", dot: "bg-neutral-400", label: c.layer };
              const isExpanded = expanded === c.layer;
              return (
                <div
                  key={c.layer}
                  className={`rounded-xl px-3 py-2 border cursor-pointer transition-all hover:shadow-sm ${style.bg}`}
                  onClick={() => setExpanded(isExpanded ? null : c.layer)}
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className={`w-2 h-2 rounded-full ${style.dot}`} />
                      <span className="font-medium text-[11px]">{style.label}</span>
                    </div>
                    <span className="font-mono text-xs">{fmtRate(c.hit_rate)}</span>
                  </div>
                  {isExpanded && (
                    <div className="mt-2 pt-2 border-t border-current/10 space-y-1">
                      <div className="flex justify-between text-neutral-500">
                        <span>命中</span><span className="font-mono">{c.hits}</span>
                      </div>
                      <div className="flex justify-between text-neutral-500">
                        <span>未命中</span><span className="font-mono">{c.misses}</span>
                      </div>
                      <div className="flex justify-between text-neutral-500">
                        <span>淘汰</span><span className="font-mono">{c.evictions}</span>
                      </div>
                      <div className="flex justify-between text-neutral-500">
                        <span>大小</span><span className="font-mono">{c.size}/{c.maxsize}</span>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {/* Token consumption */}
        <div>
          <div className="flex items-center gap-1.5 text-neutral-700 font-medium mb-2">
            <TrendingUp className="w-3.5 h-3.5 text-neutral-400" />
            <span className="text-neutral-500">消耗统计</span>
            <button
              onClick={async () => {
                if (confirm("确定清空所有历史消耗记录？此操作不可撤销。")) {
                  await resetCostHistory();
                  load();
                }
              }}
              className="ml-auto p-1 hover:bg-red-50 rounded-lg transition-colors group"
              title="清空历史"
            >
              <Trash2 className="w-3 h-3 text-neutral-400 group-hover:text-red-500" />
            </button>
          </div>
          <div className="grid grid-cols-2 gap-1.5">
            {[
              { label: "LLM 调用", value: `${data.cost.today?.call_count ?? 0} 次` },
              { label: "总 Tokens", value: fmtTokens(data.cost.today?.total_tokens ?? 0) },
              { label: "输入/输出", value: `${fmtTokens(data.cost.today?.prompt_tokens ?? 0)} / ${fmtTokens(data.cost.today?.completion_tokens ?? 0)}` },
              { label: "累计费用", value: fmtCost(data.cost.all_time.cost || 0) },
            ].map((item) => (
              <div key={item.label} className="bg-white border border-neutral-200 rounded-xl px-2.5 py-2">
                <div className="text-neutral-400 text-[10px] mb-0.5">{item.label}</div>
                <div className="font-mono text-xs text-neutral-800 font-medium">{item.value}</div>
              </div>
            ))}
          </div>
        </div>

        {/* Budget */}
        {data.budget && (
          <div>
            <div className="flex items-center gap-1.5 text-neutral-700 font-medium mb-2">
              <DollarSign className="w-3.5 h-3.5 text-neutral-400" />
              <span className="text-neutral-500">预算</span>
              {budgetDanger && <AlertTriangle className="w-3.5 h-3.5 text-red-500" />}
              {budgetWarning && !budgetDanger && <AlertTriangle className="w-3.5 h-3.5 text-amber-500" />}
              <button onClick={() => setEditBudget(!editBudget)}
                className="ml-1 p-0.5 hover:bg-neutral-100 rounded transition-colors" title="修改预算">
                <Settings2 className={`w-3.5 h-3.5 ${editBudget ? "text-primary-500" : "text-neutral-400"}`} />
              </button>
              <span className="ml-auto font-mono text-neutral-400">{fmtCost(data.budget.daily_remaining)}</span>
            </div>

            {/* Daily bar */}
            <div className="w-full bg-neutral-100 rounded-full h-1.5 overflow-hidden">
              <div className={`h-full rounded-full transition-all duration-500 ${budgetDanger ? "bg-red-500" : budgetWarning ? "bg-amber-500" : "bg-emerald-500"}`}
                style={{ width: `${Math.min(budgetRatio * 100, 100)}%` }} />
            </div>
            <div className="flex justify-between mt-0.5 text-neutral-400 text-[10px]">
              <span>日 ¥{data.budget.daily_cost.toFixed(4)} / ¥{data.budget.daily_budget}</span>
              <span>{data.budget.degrade_strategy === "reject" ? "超限拒绝" : data.budget.degrade_strategy === "cache_only" ? "仅缓存" : "仅警告"}</span>
            </div>

            {/* All-time bar */}
            <div className="mt-2 w-full bg-neutral-100 rounded-full h-1 overflow-hidden">
              <div className={`h-full rounded-full transition-all duration-500 ${allTimeDanger ? "bg-red-400" : allTimeWarning ? "bg-amber-400" : "bg-primary-400"}`}
                style={{ width: `${Math.min(allTimeRatio * 100, 100)}%` }} />
            </div>
            <div className="flex justify-between mt-0.5 text-neutral-400 text-[10px]">
              <span>累计 ¥{fmtCost(data.budget.all_time_cost)} / ¥{data.budget.all_time_budget}</span>
              <span className={allTimeWarning ? "text-amber-500 font-medium" : ""}>{(allTimeRatio * 100).toFixed(0)}%</span>
            </div>

            {/* Alerts */}
            {(dailyAlert || allTimeAlert) && (
              <div className="mt-2 space-y-1">
                {dailyAlert && (
                  <div className="flex items-center gap-1.5 px-2 py-1.5 rounded-xl bg-red-50 border border-red-200 text-red-600 text-[11px]">
                    <AlertTriangle className="w-3.5 h-3.5 shrink-0" />{dailyAlert}
                  </div>
                )}
                {allTimeAlert && (
                  <div className="flex items-center gap-1.5 px-2 py-1.5 rounded-xl bg-amber-50 border border-amber-200 text-amber-700 text-[11px]">
                    <AlertTriangle className="w-3.5 h-3.5 shrink-0" />{allTimeAlert}
                  </div>
                )}
              </div>
            )}

            {/* Budget edit form */}
            {editBudget && (
              <div className="mt-2 p-2.5 rounded-xl bg-white border border-neutral-200 space-y-2 shadow-sm">
                <div className="flex items-center gap-2">
                  <label className="text-neutral-400 w-14 shrink-0 text-[11px]">日预算</label>
                  <input type="number" min={1} step={10} value={editForm.daily}
                    onChange={e => setEditForm(f => ({...f, daily: Number(e.target.value)}))}
                    className="flex-1 px-2 py-1 border border-neutral-200 rounded-lg text-xs font-mono bg-white focus:border-primary-300 focus:ring-1 focus:ring-primary-100 outline-none" />
                  <span className="text-neutral-400 text-[10px]">¥</span>
                </div>
                <div className="flex items-center gap-2">
                  <label className="text-neutral-400 w-14 shrink-0 text-[11px]">累计预算</label>
                  <input type="number" min={1} step={50} value={editForm.all_time}
                    onChange={e => setEditForm(f => ({...f, all_time: Number(e.target.value)}))}
                    className="flex-1 px-2 py-1 border border-neutral-200 rounded-lg text-xs font-mono bg-white focus:border-primary-300 focus:ring-1 focus:ring-primary-100 outline-none" />
                  <span className="text-neutral-400 text-[10px]">¥</span>
                </div>
                <div className="flex items-center gap-2">
                  <label className="text-neutral-400 w-14 shrink-0 text-[11px]">策略</label>
                  <select value={editForm.degrade}
                    onChange={e => setEditForm(f => ({...f, degrade: e.target.value}))}
                    className="flex-1 px-2 py-1 border border-neutral-200 rounded-lg text-xs bg-white focus:border-primary-300 focus:ring-1 focus:ring-primary-100 outline-none">
                    <option value="warn">仅警告</option>
                    <option value="cache_only">仅缓存</option>
                    <option value="reject">超限拒绝</option>
                  </select>
                </div>
                <button onClick={async () => {
                    await updateBudget({ daily: editForm.daily, all_time: editForm.all_time, degrade: editForm.degrade });
                    setEditBudget(false); load();
                  }}
                  className="w-full flex items-center justify-center gap-1 px-2 py-1.5 rounded-xl bg-primary-600 text-white text-xs font-medium hover:bg-primary-700 transition-all shadow-sm active:scale-[0.98]">
                  <Check className="w-3 h-3" />保存
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
