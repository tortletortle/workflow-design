"""权限审核:工具创建时声明权限(permission),分发时按命令种类(category)限制。

两层模型:
- 工具级:每个工具在 @tool(..., permission="shell") 创建时声明所需权限;
  PermissionPolicy.allowed_permissions 是本次 run 授予的权限集合,未授予 → 拒绝
- 命令级:对带 command / program 参数的工具(终端/命令/PowerShell),按内置规则表
  把命令分类为 read / write / execute / destructive 四档;
  超出 allowed_categories 允许的档位 → 拒绝

危险等级:read(0) < write(1) < execute(2) < destructive(3);
允许高档位自动涵盖低档位。未知命令默认按 execute 档保守处理(allow_unknown 可放开)。

拒绝不抛异常:分发器返回 ToolResult(ok=False, error="permission denied: ..."),
错误文本回填给模型,模型可改用低危命令重试 —— 审核是可观测、可恢复的。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

CATEGORY_RANK = {"read": 0, "write": 1, "execute": 2, "destructive": 3}
UNKNOWN_RANK = 2  # 未知命令保守按 execute 档

# 规则表:命令头(前 1~2 个 token,小写)→ 类别。可用 register_rule 扩展。
RULES: dict[str, str] = {
    # ---- read ----
    "echo": "read", "dir": "read", "ls": "read", "cat": "read", "type": "read",
    "pwd": "read", "cd": "read", "whoami": "read", "hostname": "read",
    "date": "read", "time": "read", "ver": "read",
    "find": "read", "findstr": "read", "grep": "read", "select-string": "read",
    "wc": "read", "head": "read", "tail": "read", "which": "read",
    "get-content": "read", "get-item": "read", "get-childitem": "read",
    "get-service": "read", "get-process": "read", "get-date": "read",
    "test-path": "read", "measure-object": "read", "sort-object": "read",
    "git status": "read", "git log": "read", "git diff": "read", "git show": "read",
    "git branch": "read", "git remote": "read", "git tag": "read", "git blame": "read",
    "python --version": "read", "pip list": "read", "pip show": "read",
    "npm ls": "read", "npm list": "read", "uv --version": "read",
    # ---- write ----
    "mkdir": "write", "md": "write", "touch": "write", "cp": "write", "copy": "write",
    "mv": "write", "move": "write", "ren": "write", "rename": "write",
    "new-item": "write", "set-content": "write", "add-content": "write",
    "out-file": "write", "tee": "write", "export-csv": "write",
    "set-variable": "write", "set-itemproperty": "write",
    "git add": "write", "git commit": "write", "git checkout": "write",
    "git clone": "write", "git pull": "write", "git stash": "write",
    "git merge": "write", "git rebase": "write", "git reset": "write", "git restore": "write",
    "pip install": "write", "pip uninstall": "write",
    "npm install": "write", "npm uninstall": "write", "npm update": "write",
    "uv sync": "write", "uv add": "write", "uv remove": "write",
    # ---- execute ----
    "python": "execute", "py": "execute", "node": "execute",
    "npm run": "execute", "npm test": "execute", "npx": "execute",
    "bash": "execute", "sh": "execute", "zsh": "execute",
    "cmd": "execute", "powershell": "execute", "pwsh": "execute",
    "start": "execute", "start-process": "execute",
    "invoke-expression": "execute", "iex": "execute",
    "wscript": "execute", "cscript": "execute",
    "uv": "execute", "pytest": "execute", "make": "execute",
    "git push": "execute",
    # ---- destructive ----
    "rm": "destructive", "rmdir": "destructive", "rd": "destructive",
    "del": "destructive", "erase": "destructive",
    "remove-item": "destructive", "ri": "destructive",
    "clear-content": "destructive", "clear-item": "destructive",
    "format": "destructive", "format-volume": "destructive",
    "mkfs": "destructive", "dd": "destructive",
    "shutdown": "destructive", "restart-computer": "destructive", "stop-computer": "destructive",
    "stop-process": "destructive", "kill": "destructive", "taskkill": "destructive",
    "stop-service": "destructive",
    "reg delete": "destructive", "remove-itemproperty": "destructive",
    "truncate": "destructive", "drop": "destructive",
    "git clean": "destructive",
}

_SEGMENT_SPLIT = re.compile(r"[|;]|&&|\|\|")
# cmd / powershell 包装器:内部命令递归分类
_WRAPPER_INNER = {"cmd": "/c", "powershell": "-command", "pwsh": "-command", "bash": "-c", "sh": "-c"}


def register_rule(head: str, category: str) -> None:
    """扩展规则表,如 register_rule("git rename", "write")。"""
    if category not in CATEGORY_RANK:
        raise ValueError(f"unknown category '{category}'; valid: {sorted(CATEGORY_RANK)}")
    RULES[head.lower()] = category


def _ps_verb_category(head: str) -> str | None:
    """PowerShell 动词前缀推断:Get-* → read,New-*/Set-* → write 等。"""
    if head.startswith(("get-", "test-", "write-output", "write-host")):
        return "read"
    if head.startswith(("set-", "new-", "add-", "out-", "export-", "import-", "update-", "write-")):
        return "write"
    if head.startswith(("remove-", "clear-", "stop-", "reset-")):
        return "destructive"
    if head.startswith(("start-", "invoke-")):
        return "execute"
    return None


def _classify_head(tokens: list[str]) -> str:
    head = tokens[0].lower().removesuffix(".exe").lstrip("&").lstrip("(")
    if head in _WRAPPER_INNER:
        inner = _extract_inner(head, tokens)
        return classify_command(inner) if inner.strip() else "execute"
    two = None
    if len(tokens) > 1 and not tokens[1].startswith(("-", "/")):
        two = f"{head} {tokens[1].lower()}"
    return RULES.get(two or "") or RULES.get(head) or _ps_verb_category(head) or "unknown"


def _extract_inner(wrapper: str, tokens: list[str]) -> str:
    """cmd /c X、powershell -Command X → 提取 X 再分类。"""
    marker = _WRAPPER_INNER[wrapper]
    for i, t in enumerate(tokens):
        if t.lower() == marker:
            return " ".join(tokens[i + 1 :])
    return ""


def classify_command(command: str) -> str:
    """整条命令 → 类别;多段(管道/; /&&)取危险等级最高的一段。

    含 > / >> 重定向时,整体至少升到 write 档。
    """
    command = (command or "").strip()
    if not command:
        return "unknown"
    worst_cat, worst_rank = "unknown", -1
    for segment in _SEGMENT_SPLIT.split(command):
        tokens = segment.replace('"', " ").split()
        if not tokens:
            continue
        cat = _classify_head(tokens)
        rank = CATEGORY_RANK.get(cat, UNKNOWN_RANK) if cat != "unknown" else UNKNOWN_RANK
        if rank > worst_rank:
            worst_cat, worst_rank = cat, rank
    if re.search(r">", command) and worst_rank < CATEGORY_RANK["write"]:
        worst_cat, worst_rank = "write", CATEGORY_RANK["write"]
    return worst_cat if worst_rank >= 0 else "unknown"


@dataclass
class Decision:
    allowed: bool
    reason: str
    category: str | None = None
    permission: str | None = None
    x_meta: dict = field(default_factory=dict)


class PermissionPolicy:
    """一次 run 的权限策略。默认最严:只授 default 权限、只放行 read 档命令。"""

    def __init__(
        self,
        allowed_categories: tuple[str, ...] | set[str] = ("read",),
        allowed_permissions: tuple[str, ...] | set[str] = ("default",),
        allow_unknown: bool = False,
        blocked_tools: tuple[str, ...] | set[str] = (),
    ):
        self.allowed_categories = set(allowed_categories)
        self.allowed_permissions = set(allowed_permissions)
        self.allow_unknown = allow_unknown
        self.blocked_tools = set(blocked_tools)

    def _max_rank(self) -> int:
        return max((CATEGORY_RANK[c] for c in self.allowed_categories), default=-1)

    def check(self, tool_name: str, permission: str | None, arguments: dict) -> Decision:
        """工具级 + 命令级两层审核;permission=None 视为 'default'。"""
        if tool_name in self.blocked_tools:
            return Decision(False, f"tool '{tool_name}' is blocked by policy", permission=permission)

        permission = permission or "default"
        if permission not in self.allowed_permissions:
            return Decision(
                False,
                f"tool '{tool_name}' requires permission '{permission}', "
                f"granted: {sorted(self.allowed_permissions)}",
                permission=permission,
            )

        # 命令级:凡是参数里带 command / program 的工具都做分类审核
        command = self._extract_command(arguments)
        if command:
            category = classify_command(command)
            rank = CATEGORY_RANK.get(category, UNKNOWN_RANK)
            if category == "unknown" and self.allow_unknown:
                rank = CATEGORY_RANK["read"]
            if rank > self._max_rank():
                return Decision(
                    False,
                    f"command category '{category}' exceeds allowed categories "
                    f"{sorted(self.allowed_categories)} (command: {command[:120]})",
                    category=category,
                    permission=permission,
                )
            return Decision(True, "ok", category=category, permission=permission)

        return Decision(True, "ok", permission=permission)

    @staticmethod
    def _extract_command(arguments: dict) -> str | None:
        """从工具参数中提取待审核命令:command 字符串,或 program+arguments 组装。"""
        if not isinstance(arguments, dict):
            return None
        if isinstance(arguments.get("command"), str) and arguments["command"].strip():
            return arguments["command"]
        program = arguments.get("program")
        if isinstance(program, str) and program.strip():
            args = arguments.get("arguments") or []
            if isinstance(args, (list, tuple)):
                return " ".join([program, *map(str, args)])
            return program
        return None
