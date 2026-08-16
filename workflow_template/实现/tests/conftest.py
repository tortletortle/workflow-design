"""conftest:纯离线测试基线,零网络零外部服务。"""
import os

os.environ.pop("GW_API_KEY", None)
os.environ.pop("API_KEY", None)
