"""临时测试脚本:测试图像生成API连接。

警告:此文件不是正式测试套件的一部分。
正式测试请运行: uv run pytest

使用方法:
1. 确保 .env 文件配置正确
2. 运行: uv run python test_image_api.py
"""
import asyncio
from pathlib import Path
from app.settings import EnvSettings
from app.client import ImageClient

async def test_image_gen():
    """使用项目配置测试图像生成。"""
    try:
        # 从 .env 加载配置
        env = EnvSettings()
        
        if not env.api_key:
            print("错误: .env 中未配置 COMIC_API_KEY")
            return
        
        print(f"使用端点: {env.base_url}")
        print(f"使用模型: {env.image_model}")
        
        # 创建客户端
        client = ImageClient(env)
        
        try:
            # 测试生成图片
            print("\n开始生成图片...")
            data = await client.generate(
                "A simple black cat sitting on a white background",
                "1024x1024"
            )
            
            # 保存测试图片
            test_output = Path("test_output.png")
            test_output.write_bytes(data)
            
            print(f"✓ 成功生成图片: {len(data)} 字节")
            print(f"✓ 已保存到: {test_output}")
            
        finally:
            await client.aclose()
            
    except Exception as e:
        print(f"✗ 错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_image_gen())
