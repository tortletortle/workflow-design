"""风格一致性:全局风格锚 + 角色/主体卡库 + 风格库。

跨任务一致性的三层机制(由弱到强,可叠加):
1. 全局风格锚(config.yaml style.anchor):所有生成公共前缀,如 "16-bit pixel art"。
2. 风格库(lib/styles/):命名的可复用风格,generate_asset(style="像素风") 按名引用。
3. 角色卡库(lib/subjects/):命名的主体外观卡,生成时注入"必须精确匹配"约束;
   save_subject_as 把当次生效的主体描述冻结成卡,下次 subject_ref 引用即得同款外观。

存储为 JSON 文件,人可读可手改。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from app.settings import AppConfig, PROJECT_ROOT


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:32].strip("-") or "asset"


def _name_slug(name: str) -> str:
    """库文件名清洗:保留中文等 Unicode(卡名是用户面对的名字,不能都塌缩成 asset),
    仅去掉路径非法字符与空白。"""
    slug = re.sub(r'[\s/\\:*?"<>|]+', "-", name.strip()).strip("-.")
    return slug[:48] or "unnamed"


def _lib_dir(cfg: AppConfig | None) -> Path:
    root = PROJECT_ROOT / (cfg.style.lib_dir if cfg else "lib")
    root.mkdir(parents=True, exist_ok=True)
    return root


# ---------- 主体/角色卡 ----------

def subject_path(name: str, cfg: AppConfig | None = None) -> Path:
    return _lib_dir(cfg) / "subjects" / f"{_name_slug(name)}.json"


def load_subject(name: str, cfg: AppConfig | None = None) -> dict | None:
    """按名称读主体卡(兼容旧 ascii slug 文件);不存在返回 None。"""
    path = subject_path(name, cfg)
    if not path.exists():
        legacy = _lib_dir(cfg) / "subjects" / f"{slugify(name)}.json"
        if legacy.exists():
            path = legacy
        else:
            return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_subject(name: str, card: str, style: str = "", task_id: str = "",
                 cfg: AppConfig | None = None) -> dict:
    """新建/更新主体卡(更新=覆盖卡面,追加样本任务号)。"""
    path = subject_path(name, cfg)
    old = {}
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    data = {
        "name": name,
        "card": card.strip(),
        "style": (style or old.get("style", "")).strip(),
        "samples": list(dict.fromkeys([*old.get("samples", []), task_id]))[-10:],
        "updated": datetime.now().isoformat(timespec="seconds"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


# ---------- 风格库 ----------

def style_path(name: str, cfg: AppConfig | None = None) -> Path:
    return _lib_dir(cfg) / "styles" / f"{_name_slug(name)}.json"


def resolve_style(style_param: str, cfg: AppConfig | None = None) -> tuple[str, str]:
    """解析 style 参数:命名风格(库命中)→ 其锚文本;否则视为字面风格描述原样返回。

    返回 (生效文本, 来源描述)。
    """
    text = (style_param or "").strip()
    if not text:
        return "", ""
    path = style_path(text, cfg)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("anchor", "").strip(), f"style-lib:{text}"
        except (OSError, json.JSONDecodeError):
            pass
    return text, "literal"


def save_style(name: str, anchor: str, cfg: AppConfig | None = None) -> dict:
    data = {
        "name": name,
        "anchor": anchor.strip(),
        "updated": datetime.now().isoformat(timespec="seconds"),
    }
    path = style_path(name, cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def list_library(cfg: AppConfig | None = None) -> dict:
    """汇总风格库与主体卡库,供 list_library 工具/调试查看。"""
    root = _lib_dir(cfg)
    styles, subjects = [], []
    for path in sorted(root.glob("styles/*.json")):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            styles.append({"name": d.get("name", path.stem), "anchor": d.get("anchor", "")})
        except (OSError, json.JSONDecodeError):
            continue
    for path in sorted(root.glob("subjects/*.json")):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            subjects.append({"name": d.get("name", path.stem), "card": d.get("card", ""),
                             "style": d.get("style", ""), "samples": d.get("samples", [])})
        except (OSError, json.JSONDecodeError):
            continue
    return {"status": "ok", "styles": styles, "subjects": subjects}
