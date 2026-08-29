"""03_游戏制作 工作流主入口

用法:
    python -m app.main "我想做一个塔防游戏，中世纪风格"
    python -m app.main "贪吃蛇" --out ./out/my-snake
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .models import Checkpoint
from . import design as stage_design
from . import template_search as stage_template
from . import asset_gen as stage_assets
from . import build as stage_build
from . import fix as stage_fix
from . import vision_qa as stage_qa


STAGES = ["design", "template", "assets", "build", "qa"]


def run_pipeline(idea: str, output_dir: Path, max_fix_rounds: int = 2) -> Checkpoint:
    """执行完整工作流"""
    output_dir.mkdir(parents=True, exist_ok=True)
    cp_path = output_dir / "checkpoint.json"
    cp = Checkpoint.load(cp_path) if cp_path.exists() else None
    
    # 恢复检查点
    design_data = cp.design if cp and cp.stage in ("template", "assets", "build", "qa", "done") else None
    template_data = cp.template if cp and cp.stage in ("assets", "build", "qa", "done") else None
    assets_data = cp.assets if cp and cp.stage in ("build", "qa", "done") else None
    game_files = cp.game_files if cp and cp.stage in ("qa", "done") else None
    
    # Stage 1: 认知
    if not design_data:
        t0 = time.time()
        design = stage_design.run(idea, output_dir)
        design_data = json.loads((output_dir / "design.json").read_text(encoding="utf-8"))
        cp = Checkpoint(stage="design", idea=idea, output_dir=str(output_dir), design=design_data)
        cp.save(cp_path)
        print(f"    ⏱ 认知耗时: {time.time()-t0:.1f}s")
    
    # Stage 2: 模板检索
    if not template_data:
        t0 = time.time()
        tmpl = stage_template.run(design_data, output_dir)
        template_data = json.loads((output_dir / "template_match.json").read_text(encoding="utf-8"))
        cp = Checkpoint(stage="template", idea=idea, output_dir=str(output_dir),
                        design=design_data, template=template_data)
        cp.save(cp_path)
        print(f"    ⏱ 模板检索耗时: {time.time()-t0:.1f}s")
    
    # Stage 3: 素材生成
    if not assets_data:
        t0 = time.time()
        assets_data = stage_assets.run(design_data, output_dir)
        cp = Checkpoint(stage="assets", idea=idea, output_dir=str(output_dir),
                        design=design_data, template=template_data, assets=assets_data)
        cp.save(cp_path)
        print(f"    ⏱ 素材生成耗时: {time.time()-t0:.1f}s")
    
    # Stage 4: 制作
    if not game_files:
        t0 = time.time()
        game_files = stage_build.run(design_data, template_data, assets_data, output_dir)
        cp = Checkpoint(stage="build", idea=idea, output_dir=str(output_dir),
                        design=design_data, template=template_data, assets=assets_data,
                        game_files=game_files)
        cp.save(cp_path)
        print(f"    ⏱ 制作耗时: {time.time()-t0:.1f}s")
    
    # Stage 5: 视觉检测（带修复循环）
    qa_results = cp.qa_results if cp and cp.stage == "done" else None
    if not qa_results:
        fix_round = 0
        while fix_round <= max_fix_rounds:
            t0 = time.time()
            qa_results = stage_qa.run(game_files, output_dir)
            print(f"    ⏱ 检测耗时: {time.time()-t0:.1f}s")
            
            # 检查是否全部通过（或跳过）
            all_passed = all(r.get("passed", False) for r in qa_results)
            if all_passed:
                break
            
            if fix_round < max_fix_rounds:
                # 收集问题
                issues = []
                for r in qa_results:
                    if not r.get("passed", False):
                        issues.extend(r.get("issues", []))
                print(f"  ⚠ 视觉检测未通过 ({len(issues)} 问题)，第 {fix_round+1} 轮修复...")
                
                fixed = False
                for gf in game_files:
                    html_path = Path(gf)
                    if html_path.suffix == ".html":
                        fixed = stage_fix.fix_code(html_path, issues, design_data.get("title", "Game"))
                
                if not fixed:
                    break
                fix_round += 1
                qa_results = None  # 重跑 QA
            else:
                print(f"  ⚠ 已达最大修复轮数 ({max_fix_rounds})，交付当前版本")
                break
        
        # 确保 qa_results 有效
        if qa_results is None:
            qa_results = []
            # 重新截图检测失败的情况已在 stage_qa 记录
        cp = Checkpoint(stage="done", idea=idea, output_dir=str(output_dir),
                        design=design_data, template=template_data, assets=assets_data,
                        game_files=game_files, qa_results=qa_results)
        cp.save(cp_path)
    else:
        print(f"  ✓ 检测结果已缓存（上一轮已完成）")
    
    return Checkpoint(
        stage="done", idea=idea, output_dir=str(output_dir),
        design=design_data, template=template_data, assets=assets_data,
        game_files=game_files, qa_results=qa_results
    )


def main():
    parser = argparse.ArgumentParser(description="AI 游戏制作工作流")
    parser.add_argument("idea", nargs="?", default="", help="游戏想法描述")
    parser.add_argument("--out", "-o", default="", help="输出目录 (默认 ./out/<timestamp>)")
    parser.add_argument("--rounds", default=2, type=int, help="视觉检测最大修复轮数")
    parser.add_argument("--stage", default="", choices=STAGES,
                        help="只运行到指定阶段 (调试用)")
    args = parser.parse_args()
    
    if not args.idea:
        print("错误: 请提供游戏描述，如: python -m app.main \"我想做一个塔防游戏\"")
        sys.exit(1)
    
    if args.out:
        out_dir = Path(args.out)
    else:
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = Path("out") / ts
    
    print(f"🎮 游戏制作工作流启动")
    print(f"   想法: {args.idea}")
    print(f"   输出: {out_dir}")
    print()
    
    t_start = time.time()
    try:
        cp = run_pipeline(args.idea, out_dir, args.rounds)
        elapsed = time.time() - t_start
        
        print()
        print(f"✅ 工作流完成! 总耗时 {elapsed:.1f}s")
        print(f"   输出目录: {out_dir}")
        print(f"   设计文档: {out_dir / 'design.json'}")
        print(f"   模板匹配: {out_dir / 'template_match.json'}")
        print(f"   游戏代码: {out_dir / 'game' / 'index.html'}")
        print(f"   素材目录: {out_dir / 'assets'}")
        print(f"   QA 报告:  {out_dir / 'qa' / 'qa_report.json'}")
        if cp.qa_results:
            scores = [r.get('score', 0) for r in cp.qa_results if r.get('score')]
            if scores:
                print(f"   QA 评分:  {sum(scores)/len(scores):.1f}/10")
        
    except Exception as e:
        print(f"\n❌ 工作流异常: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()