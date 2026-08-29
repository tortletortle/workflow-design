"""Stage 3: 素材生成 — 调用 02_游戏素材生成 工作流"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from .models import AssetResult
from .settings import ASSET_GEN_DIR


def generate_one(desc: str, category: str, style: str, save_subject: str = "") -> AssetResult:
    """调用 gen_asset.py 生成单个素材"""
    cmd = [
        sys.executable, str(ASSET_GEN_DIR / "gen_asset.py"),
        desc,
        "--category", category,
    ]
    if style:
        cmd += ["--style", style]
    if save_subject:
        cmd += ["--save-subject", save_subject]
    
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(ASSET_GEN_DIR),
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            return AssetResult(
                description=desc, category=category, style=style,
                success=False, notes=f"gen_asset 失败: {proc.stderr[-500:]}",
            )
        # 解析输出的任务目录
        out = proc.stdout
        # 从 stdout 中找任务目录/素材路径
        task_dir = None
        import re
        m = re.search(r"(?:任务目录|task_dir|输出目录)[:：\s]+([^\s]+)", out)
        if m:
            task_dir = Path(m.group(1).strip().strip('"\''))
        
        if not task_dir or not task_dir.exists():
            # 找 JSON 行
            m = re.search(r'\{.*\}', out, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group(0))
                    task_dir = Path(data.get("task_dir", data.get("output_dir", "")))
                except Exception:
                    pass
        
        finals = []
        draft = ""
        if task_dir and task_dir.exists():
            finals_dir = task_dir / "finals"
            if finals_dir.exists():
                finals = [str(p) for p in sorted(finals_dir.glob("*.png"))]
            draft_p = task_dir / "draft.png"
            if draft_p.exists():
                draft = str(draft_p)
        
        return AssetResult(
            description=desc, category=category, style=style,
            success=len(finals) > 0,
            draft_path=draft,
            finals=finals,
            notes=f"stdout: {out[:300]}",
        )
    except subprocess.TimeoutExpired:
        return AssetResult(
            description=desc, category=category, style=style,
            success=False, notes="gen_asset 超时 (>300s)",
        )
    except Exception as e:
        return AssetResult(
            description=desc, category=category, style=style,
            success=False, notes=f"异常: {e}",
        )


def make_placeholder(name: str, output_dir: Path, color: tuple[int, int, int] = (120, 120, 200)) -> Path:
    """生成占位色块"""
    try:
        from PIL import Image
    except ImportError:
        # 无 Pillow 时生成 SVG 占位
        svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256">
  <rect width="256" height="256" fill="rgb{color}"/>
  <text x="128" y="132" font-size="20" text-anchor="middle" fill="white">{name}</text>
</svg>"""
        p = output_dir / f"{name}.svg"
        p.write_text(svg, encoding="utf-8")
        return p
    
    img = Image.new("RGBA", (256, 256), (*color, 255))
    p = output_dir / f"{name}.png"
    img.save(p)
    return p


def run(design_data: dict, output_dir: Path) -> list[dict]:
    """Stage 3: 素材生成"""
    print(f"  [Stage 3/5] 素材生成: 调用 02_游戏素材生成 工作流...")
    sys.stdout.flush()
    
    assets_dir = output_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    
    assets_spec = design_data.get("assets", [])
    results = []
    
    for i, spec in enumerate(assets_spec):
        desc = spec.get("description") or spec.get("name") or f"asset_{i}"
        category = spec.get("category", "item")
        style = spec.get("style", "") or design_data.get("style", "cartoon")
        asset_id = spec.get("id", f"asset_{i}")
        
        print(f"    [{i+1}/{len(assets_spec)}] 生成: {desc} ({category})")
        sys.stdout.flush()
        
        res = generate_one(desc, category, style)
        
        if res.success:
            print(f"      ✓ 成功: {len(res.finals)} 个尺寸")
            # 复制到 assets 目录
            copied = []
            for f in res.finals[:2]:  # 取最大两个尺寸
                fpath = Path(f)
                if fpath.exists():
                    target = assets_dir / f"{asset_id}{fpath.suffix}"
                    if not target.exists():
                        target.write_bytes(fpath.read_bytes())
                    copied.append(str(target))
            res_dict = {
                "id": asset_id,
                "description": desc,
                "category": category,
                "success": True,
                "finals": copied,
                "draft": res.draft_path,
                "notes": res.notes,
            }
        else:
            print(f"      ⚠ 失败: {res.notes[:100]}")
            placeholder = make_placeholder(asset_id, assets_dir)
            res_dict = {
                "id": asset_id,
                "description": desc,
                "category": category,
                "success": False,
                "finals": [str(placeholder)],
                "draft": "",
                "notes": res.notes,
            }
        
        results.append(res_dict)
    
    # 写素材清单
    manifest_path = assets_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"    ✓ 素材清单: {manifest_path}")
    
    return results