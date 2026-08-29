#!/bin/bash
# 修复 .env 文件中的模型配置

ENV_FILE=".env"

if [ ! -f "$ENV_FILE" ]; then
    echo "错误: .env 文件不存在"
    exit 1
fi

echo "备份原始 .env 文件..."
cp .env .env.backup.$(date +%Y%m%d_%H%M%S)

echo "修改模型配置..."
sed -i 's/COMIC_IMAGE_MODEL=step-1-32k/COMIC_IMAGE_MODEL=step-image-edit-2/g' .env
sed -i 's/COMIC_VISION_MODEL=step-1-32k/COMIC_VISION_MODEL=step-image-edit-2/g' .env

echo "✓ 配置已更新"
echo ""
echo "修改内容："
grep "IMAGE_MODEL\|VISION_MODEL" .env

echo ""
echo "现在可以运行测试："
echo "  uv run python test_image_api.py"
echo "或生成漫画："
echo "  uv run python -m app.main doupo_test.txt --panels 4 -o out_doupo_test"
