"use client";

import { useState, useEffect } from "react";
import {
  fetchLlmConfig,
  updateLlmConfig,
  testLlmConnection,
} from "@/lib/api";
import {
  X,
  Cloud,
  Shield,
  Loader2,
  CheckCircle2,
  AlertCircle,
  Settings2,
  Save,
  Lock,
} from "lucide-react";

interface SettingsModalProps {
  onClose: () => void;
}

export default function SettingsModal({ onClose }: SettingsModalProps) {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testingPro, setTestingPro] = useState(false);
  const [testingSafe, setTestingSafe] = useState(false);
  const [hasSavedKey, setHasSavedKey] = useState(false);
  const [testResult, setTestResult] = useState<{
    mode: string;
    success: boolean;
    message: string;
  } | null>(null);

  // Form state — api_key starts empty, not pre-filled from API
  const [professional, setProfessional] = useState({
    api_key: "",
    base_url: "",
    model: "",
  });
  const [safe, setSafe] = useState({
    base_url: "",
    model: "",
  });

  // Load config on mount — only fill base_url & model, NOT api_key
  useEffect(() => {
    (async () => {
      try {
        const cfg = await fetchLlmConfig();
        const proKey = cfg.professional.api_key || "";
        setHasSavedKey(!!proKey);
        setProfessional({
          api_key: "", // Never pre-fill masked key
          base_url: cfg.professional.base_url || "",
          model: cfg.professional.model || "",
        });
        setSafe({
          base_url: cfg.safe.base_url || "",
          model: cfg.safe.model || "",
        });
      } catch {
        // Use defaults
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  // Build update payload — only include api_key if user typed something
  const buildProPayload = () => {
    const payload: Record<string, any> = {
      base_url: professional.base_url,
      model: professional.model,
    };
    if (professional.api_key.trim()) {
      payload.api_key = professional.api_key.trim();
    }
    return payload;
  };

  const handleSave = async () => {
    setSaving(true);
    setTestResult(null);
    try {
      await updateLlmConfig({
        professional: buildProPayload(),
        safe: {
          base_url: safe.base_url,
          model: safe.model,
        },
      });
      setTestResult({ mode: "save", success: true, message: "✅ 配置已保存" });
      if (professional.api_key.trim()) {
        setHasSavedKey(true);
        setProfessional((p) => ({ ...p, api_key: "" })); // clear field after save
      }
      setTimeout(() => setTestResult(null), 3000);
    } catch (e: any) {
      setTestResult({
        mode: "save",
        success: false,
        message: `❌ 保存失败: ${e.message}`,
      });
    } finally {
      setSaving(false);
    }
  };

  const handleTestPro = async () => {
    setTestingPro(true);
    setTestResult(null);
    try {
      // Test with inline params — no need to save first
      const res = await testLlmConnection({
        mode: "pro",
        model: professional.model,
        base_url: professional.base_url,
        api_key: professional.api_key || undefined,
      });
      setTestResult({
        mode: "pro",
        success: res.success,
        message: res.success
          ? "✅ 连接成功！"
          : `❌ ${res.message || res.error || "连接失败"}`,
      });
    } catch (e: any) {
      setTestResult({ mode: "pro", success: false, message: `❌ ${e.message}` });
    } finally {
      setTestingPro(false);
    }
  };

  const handleTestSafe = async () => {
    setTestingSafe(true);
    setTestResult(null);
    try {
      const res = await testLlmConnection({
        mode: "safe",
        model: safe.model,
        base_url: safe.base_url,
      });
      setTestResult({
        mode: "safe",
        success: res.success,
        message: res.success
          ? "✅ 连接成功！"
          : `❌ ${res.message || res.error || "连接失败"}`,
      });
    } catch (e: any) {
      setTestResult({ mode: "safe", success: false, message: `❌ ${e.message}` });
    } finally {
      setTestingSafe(false);
    }
  };

  if (loading) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20 backdrop-blur-sm animate-fade-in">
        <div className="glass rounded-2xl shadow-glass-lg p-6 flex items-center gap-2 text-sm text-neutral-500">
          <Loader2 className="w-4 h-4 animate-spin text-primary-500" />
          加载配置...
        </div>
      </div>
    );
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/20 backdrop-blur-sm animate-fade-in"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="glass rounded-2xl shadow-glass-lg w-[28rem] max-h-[85vh] overflow-y-auto animate-scale-in">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-neutral-200/60">
          <div className="flex items-center gap-2 text-sm font-semibold text-neutral-900">
            <Settings2 className="w-4 h-4 text-primary-500" />
            <span>LLM 配置</span>
          </div>
          <button
            onClick={onClose}
            className="p-1 hover:bg-neutral-100 rounded-lg transition-colors"
          >
            <X className="w-4 h-4 text-neutral-400" />
          </button>
        </div>

        <div className="p-4 space-y-4">
          {/* Professional Mode */}
          <div className="rounded-xl border border-blue-200 bg-blue-50/40 overflow-hidden">
            <div className="flex items-center gap-2 px-3.5 py-2.5 bg-blue-50 border-b border-blue-100">
              <Cloud className="w-4 h-4 text-blue-600" />
              <span className="text-sm font-semibold text-blue-800">
                专业模式
              </span>
              <span className="text-[10px] text-blue-500 ml-1">
                DeepSeek API
              </span>
            </div>
            <div className="p-3.5 space-y-2.5">
              <div>
                <label className="block text-[11px] font-medium text-neutral-500 mb-1">
                  API Key
                </label>
                <div className="relative">
                  <input
                    type="password"
                    value={professional.api_key}
                    onChange={(e) =>
                      setProfessional((p) => ({
                        ...p,
                        api_key: e.target.value,
                      }))
                    }
                    className="w-full px-3 py-2 text-sm bg-white border border-neutral-200 rounded-lg focus:border-blue-300 focus:ring-2 focus:ring-blue-100 outline-none transition-all pr-10"
                    placeholder={
                      hasSavedKey
                        ? "已保存 key · 留空则不变"
                        : "sk-..."
                    }
                  />
                  {hasSavedKey && !professional.api_key && (
                    <Lock className="absolute right-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-emerald-500" />
                  )}
                </div>
                {hasSavedKey && !professional.api_key && (
                  <p className="text-[10px] text-emerald-600 mt-1 flex items-center gap-1">
                    <Lock className="w-3 h-3" />
                    API Key 已保存，留空则不修改
                  </p>
                )}
              </div>
              <div>
                <label className="block text-[11px] font-medium text-neutral-500 mb-1">
                  Base URL
                </label>
                <input
                  type="text"
                  value={professional.base_url}
                  onChange={(e) =>
                    setProfessional((p) => ({
                      ...p,
                      base_url: e.target.value,
                    }))
                  }
                  className="w-full px-3 py-2 text-sm bg-white border border-neutral-200 rounded-lg focus:border-blue-300 focus:ring-2 focus:ring-blue-100 outline-none transition-all"
                  placeholder="https://api.deepseek.com"
                />
              </div>
              <div>
                <label className="block text-[11px] font-medium text-neutral-500 mb-1">
                  模型
                </label>
                <input
                  type="text"
                  value={professional.model}
                  onChange={(e) =>
                    setProfessional((p) => ({ ...p, model: e.target.value }))
                  }
                  className="w-full px-3 py-2 text-sm bg-white border border-neutral-200 rounded-lg focus:border-blue-300 focus:ring-2 focus:ring-blue-100 outline-none transition-all"
                  placeholder="deepseek-chat"
                />
              </div>
              <button
                onClick={handleTestPro}
                disabled={testingPro}
                className="w-full flex items-center justify-center gap-1.5 px-3 py-2 rounded-xl bg-blue-600 text-white text-xs font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-all active:scale-[0.98]"
              >
                {testingPro ? (
                  <>
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    测试中...
                  </>
                ) : (
                  <>
                    <CheckCircle2 className="w-3.5 h-3.5" />
                    测试连接
                  </>
                )}
              </button>
            </div>
          </div>

          {/* Safe Mode */}
          <div className="rounded-xl border border-emerald-200 bg-emerald-50/40 overflow-hidden">
            <div className="flex items-center gap-2 px-3.5 py-2.5 bg-emerald-50 border-b border-emerald-100">
              <Shield className="w-4 h-4 text-emerald-600" />
              <span className="text-sm font-semibold text-emerald-800">
                安全模式
              </span>
              <span className="text-[10px] text-emerald-500 ml-1">
                Ollama 本地
              </span>
            </div>
            <div className="p-3.5 space-y-2.5">
              <div>
                <label className="block text-[11px] font-medium text-neutral-500 mb-1">
                  Base URL
                </label>
                <input
                  type="text"
                  value={safe.base_url}
                  onChange={(e) =>
                    setSafe((s) => ({ ...s, base_url: e.target.value }))
                  }
                  className="w-full px-3 py-2 text-sm bg-white border border-neutral-200 rounded-lg focus:border-emerald-300 focus:ring-2 focus:ring-emerald-100 outline-none transition-all"
                  placeholder="http://localhost:11434/v1"
                />
              </div>
              <div>
                <label className="block text-[11px] font-medium text-neutral-500 mb-1">
                  模型
                </label>
                <input
                  type="text"
                  value={safe.model}
                  onChange={(e) =>
                    setSafe((s) => ({ ...s, model: e.target.value }))
                  }
                  className="w-full px-3 py-2 text-sm bg-white border border-neutral-200 rounded-lg focus:border-emerald-300 focus:ring-2 focus:ring-emerald-100 outline-none transition-all"
                  placeholder="qwen2.5:1.5b"
                />
              </div>
              <button
                onClick={handleTestSafe}
                disabled={testingSafe}
                className="w-full flex items-center justify-center gap-1.5 px-3 py-2 rounded-xl bg-emerald-600 text-white text-xs font-medium hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed transition-all active:scale-[0.98]"
              >
                {testingSafe ? (
                  <>
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    测试中...
                  </>
                ) : (
                  <>
                    <CheckCircle2 className="w-3.5 h-3.5" />
                    测试连接
                  </>
                )}
              </button>
            </div>
          </div>

          {/* Test result toast */}
          {testResult && (
            <div
              className={`flex items-center gap-2 px-3.5 py-2.5 rounded-xl text-sm ${
                testResult.success
                  ? "bg-emerald-50 border border-emerald-200 text-emerald-700"
                  : "bg-red-50 border border-red-200 text-red-600"
              }`}
            >
              {testResult.success ? (
                <CheckCircle2 className="w-4 h-4 shrink-0" />
              ) : (
                <AlertCircle className="w-4 h-4 shrink-0" />
              )}
              <span>{testResult.message}</span>
            </div>
          )}

          {/* Save button */}
          <button
            onClick={handleSave}
            disabled={saving}
            className="w-full flex items-center justify-center gap-1.5 px-4 py-2.5 rounded-xl bg-primary-600 text-white text-sm font-medium hover:bg-primary-700 disabled:opacity-50 disabled:cursor-not-allowed transition-all shadow-sm active:scale-[0.98]"
          >
            {saving ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                保存中...
              </>
            ) : (
              <>
                <Save className="w-4 h-4" />
                保存配置
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
