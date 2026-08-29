import asyncio
from pathlib import Path
from app.pipeline import ComicPipeline
from app.progress import ProgressReporter
from app.settings import load_app_config

DOUPO_STORY = """第一格：测验魔石碑前，少年萧炎紧握拳头，面无表情地望着碑上闪烁的三段字样。手掌因用力指甲扎进掌心，带来钻心的疼痛。测验人员漠然地宣布了这个低级的成绩。

第二格：广场中传来嘲讽的骚动和议论声。萧炎缓缓抬起头，漆黑的眸子木然扫过那些嘲讽的同龄人。他安静地转身走向队伍末尾，孤单的身影与世界格格不入。

第三格：测验继续，一名少女萧媚快速跑出。她触摸魔石碑，碑上亮起光芒。测验员喊出七段高级的成绩。少女脸颊扬起得意的笑容。而在队伍末尾的萧炎孤独地看着这一切。"""

async def main():
    env, config = load_app_config()
    out_dir = Path("out_doupo")
    pipeline = ComicPipeline(env, config, out_dir, reporter=ProgressReporter())
    report = await pipeline.run(DOUPO_STORY, panel_count=3)
    print(report.to_json())

if __name__ == "__main__":
    asyncio.run(main())
