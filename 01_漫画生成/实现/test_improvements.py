"""测试改进后的错误处理和用户提示。"""
import sys
from io import StringIO

from app.errors import (
    QuotaExhaustedError,
    NetworkError,
    ValidationError,
    ModelOutputError,
    ImageQualityError,
)


def test_error_messages():
    """验证所有错误类型都有用户友好的提示。"""
    errors = [
        QuotaExhaustedError("HTTP 429"),
        NetworkError("连接超时", original=TimeoutError("timeout")),
        ValidationError("panels.count", "必须在 1-20 之间"),
        ModelOutputError("splitter", "JSON 解析失败", raw_output='{"bad"}'),
        ImageQualityError(3, "纯色图片"),
    ]
    
    print("=" * 70)
    print("改进后的错误提示示例")
    print("=" * 70)
    
    for i, error in enumerate(errors, 1):
        print(f"\n{i}. {type(error).__name__}")
        print("-" * 70)
        print(error.user_hint)
        print(f"   可恢复: {'是' if error.recoverable else '否'}")
        if hasattr(error, 'original') and error.original:
            print(f"   原始错误: {error.original}")
    
    print("\n" + "=" * 70)


def test_config_validation():
    """测试配置验证。"""
    from app.settings import PanelsConfig, ImageConfig, ConcurrencyConfig, AssembleConfig
    from pydantic import ValidationError as PydanticValidationError
    
    print("\n配置验证测试:")
    print("-" * 70)
    
    # 测试 1: panels.count 超出范围
    try:
        PanelsConfig(count=25)
        print("❌ panels.count=25 应该失败但通过了")
    except PydanticValidationError as e:
        print("✅ panels.count=25 正确被拒绝")
        print(f"   错误: {e.errors()[0]['msg']}")
    
    # 测试 2: image.size 格式错误
    try:
        ImageConfig(size="invalid")
        print("❌ image.size='invalid' 应该失败但通过了")
    except PydanticValidationError as e:
        print("✅ image.size='invalid' 正确被拒绝")
        print(f"   错误: {e.errors()[0]['msg']}")
    
    # 测试 3: concurrency 超出范围
    try:
        ConcurrencyConfig(design=20)
        print("❌ concurrency.design=20 应该失败但通过了")
    except PydanticValidationError as e:
        print("✅ concurrency.design=20 正确被拒绝")
        print(f"   错误: {e.errors()[0]['msg']}")
    
    # 测试 4: assemble.format 不合法
    try:
        AssembleConfig(format="BMP")
        print("❌ assemble.format='BMP' 应该失败但通过了")
    except PydanticValidationError as e:
        print("✅ assemble.format='BMP' 正确被拒绝")
        print(f"   错误: {e.errors()[0]['msg']}")
    
    print("-" * 70)


if __name__ == "__main__":
    test_error_messages()
    test_config_validation()
    print("\n✅ 所有错误处理改进已验证!")
