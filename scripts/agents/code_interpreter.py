"""
finRAG Agent — 代码解释器 (CodeInterpreter)

在本地安全沙箱中执行 LLM 生成的 Python 代码，用于精确的财务计算。
解决 LLM 算错数的问题。

安全措施：
1. 只允许白名单的库（math, numpy, pandas, statistics）
2. 禁止文件操作、网络访问、子进程
3. 超时保护（防止死循环）
4. 结果严格限制大小

用法:
    from scripts.agents.code_interpreter import CodeInterpreter
    ci = CodeInterpreter()
    result = ci.execute("print(100 * (1 + 0.1) ** 3)")
    # → {"success": True, "output": "133.1", "stdout": "133.1", "execution_time": 0.001}
"""

import ast
import logging
import math
import sys
import io
import textwrap
import time
import traceback
from typing import Optional

logger = logging.getLogger("agent.code_interpreter")

# ── 安全白名单 ─────────────────────────────────────────────────
ALLOWED_MODULES = {
    "math", "numpy", "statistics", "json", "re", "collections",
    "itertools", "functools", "datetime",
}

BLOCKED_BUILTINS = {
    "__import__", "exec", "eval", "compile", "open", "file",
    "input", "breakpoint", "exit", "quit",
}

SAFE_BUILTINS = {
    "abs": abs, "all": all, "any": any, "bool": bool, "chr": chr,
    "complex": complex, "dict": dict, "divmod": divmod, "enumerate": enumerate,
    "filter": filter, "float": float, "format": format, "frozenset": frozenset,
    "hash": hash, "hex": hex, "id": id, "int": int, "isinstance": isinstance,
    "issubclass": issubclass, "iter": iter, "len": len, "list": list,
    "map": map, "max": max, "min": min, "next": next, "object": object,
    "oct": oct, "ord": ord, "pow": pow, "print": print, "range": range,
    "repr": repr, "reversed": reversed, "round": round, "set": set,
    "slice": slice, "sorted": sorted, "str": str, "sum": sum,
    "super": super, "tuple": tuple, "type": type, "zip": zip,
    "True": True, "False": False, "None": None,
    # 常用
    "isinstance": isinstance, "hasattr": hasattr, "getattr": getattr,
    "setattr": setattr,
}

MAX_OUTPUT_LENGTH = 50000  # 字符
MAX_EXECUTION_TIME = 10    # 秒


class CodeInterpreter:
    """安全 Python 代码解释器"""

    def __init__(self):
        self._namespace = None

    def _build_safe_namespace(self) -> dict:
        """构建安全的命名空间"""
        safe_ns = {
            "__builtins__": {},
            **{k: v for k, v in SAFE_BUILTINS.items()},
        }
        # 导入常用库
        import numpy as np  # type: ignore
        safe_ns["math"] = math
        safe_ns["np"] = np
        safe_ns["numpy"] = np

        import statistics
        safe_ns["statistics"] = statistics
        safe_ns["stats"] = statistics

        safe_ns["json"] = __import__("json")
        safe_ns["re"] = __import__("re")
        safe_ns["collections"] = __import__("collections")
        safe_ns["datetime"] = __import__("datetime")
        safe_ns["itertools"] = __import__("itertools")
        safe_ns["functools"] = __import__("functools")

        return safe_ns

    def _validate_code(self, code: str) -> Optional[str]:
        """验证代码安全性，返回错误信息或 None"""
        # 检查不可信模块
        for mod in ["os", "subprocess", "shutil", "socket", "requests",
                     "httpx", "pathlib", "sys", "glob", "atexit", "signal"]:
            if mod in code and (f"import {mod}" in code or f"from {mod}" in code):
                return f"禁止使用模块: {mod}"

        # 检查危险调用
        for dangerous in ["__import__", "exec(", "eval(", "compile(",
                           "open(", "os.", "subprocess.", "socket."]:
            if dangerous in code:
                return f"禁止使用危险调用: {dangerous}"

        # 语法检查
        try:
            ast.parse(code)
        except SyntaxError as e:
            return f"代码语法错误: {e}"

        return None

    def execute(self, code: str, input_data: dict = None) -> dict:
        """
        安全执行 Python 代码（带超时保护）。

        参数:
            code: Python 代码字符串
            input_data: 可选的输入数据，注入到命名空间中

        返回:
            {"success": bool, "output": str, "stdout": str, "execution_time": float, "error": str}
        """
        # 安全检查
        error_msg = self._validate_code(code)
        if error_msg:
            return {"success": False, "output": "", "stdout": "", 
                    "execution_time": 0, "error": error_msg}

        # 准备命名空间
        namespace = self._build_safe_namespace()
        if input_data:
            namespace.update(input_data)

        # 捕获 stdout
        stdout_capture = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = stdout_capture

        # ── 带超时的执行 ───────────────────────────────────────
        import threading
        exec_result = {"exception": None, "locals": {}}

        def _run():
            try:
                exec_globals = {"__builtins__": namespace}
                exec_locals = {}
                exec(compile(textwrap.dedent(code), "<sandbox>", "exec"),
                     exec_globals, exec_locals)
                exec_result["locals"] = exec_locals
            except Exception as e:
                exec_result["exception"] = e

        thread = threading.Thread(target=_run, daemon=True)
        start = time.time()
        thread.start()
        thread.join(timeout=MAX_EXECUTION_TIME)
        elapsed = time.time() - start

        # 恢复 stdout
        sys.stdout = old_stdout

        # ── 结果处理 ───────────────────────────────────────────
        if thread.is_alive():
            # 超时：线程仍在运行，但 daemon=True 会在主线程退出时终止
            return {
                "success": False, "output": "", "stdout": stdout_capture.getvalue(),
                "execution_time": elapsed,
                "error": f"执行超时（>{MAX_EXECUTION_TIME}s）",
            }

        if exec_result["exception"]:
            exc = exec_result["exception"]
            tb = "".join(traceback.format_exception_only(type(exc), exc))
            return {
                "success": False, "output": "", "stdout": stdout_capture.getvalue(),
                "execution_time": elapsed, "error": tb,
            }

        stdout_text = stdout_capture.getvalue()[:MAX_OUTPUT_LENGTH]
        output = stdout_text

        # 尝试获取最后一个表达式的值
        exec_locals = exec_result["locals"]
        if exec_locals:
            last_var = list(exec_locals.keys())[-1]
            val = exec_locals[last_var]
            output += f"\n结果: {val}"

        return {
            "success": True,
            "output": output.strip(),
            "stdout": stdout_text.strip(),
            "execution_time": elapsed,
            "error": "",
        }

    @staticmethod
    def build_code_prompt(query: str, data_context: str, metrics_vars: str = "") -> str:
        """构建代码生成 Prompt。"""
        metrics_section = ""
        if metrics_vars:
            metrics_section = f"""
--- 预处理指标变量（可直接引用） ---
{metrics_vars}

"""

        return f"""你是一个金融计算助手。用户需要你写 Python 代码来完成计算。

用户问题: {query}

可用检索到的数据:
{data_context[:2000]}
{metrics_section}
要求：
1. 只使用 math, numpy, statistics 等基本库
2. 所有数据直接写在代码里（不要从文件/网络读取）
3. 用 print() 输出最终结果
4. 结果保留 2 位小数
5. 在代码前用 # 注释解释每一步
6. 如果上面有"预处理指标变量"，优先引用这些变量名

请只返回 Python 代码，不要额外文字。代码放在 ```python ``` 标记中。"""

    @staticmethod
    def extract_code(llm_output: str) -> str:
        """从 LLM 输出中提取 Python 代码。"""
        import re
        m = re.search(r"```(?:python)?\s*\n(.*?)```", llm_output, re.DOTALL)
        if m:
            return m.group(1).strip()
        # 如果没有代码块标记，整个返回
        return llm_output.strip()
