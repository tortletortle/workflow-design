import asyncio
import httpx

async def test_stepfun_api():
    async with httpx.AsyncClient() as client:
        # 测试基本连接
        try:
            resp = await client.post(
                "https://api.stepfun.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer 7oT07Obvcix4wli3Ui6d9RYzBK7Vqt17f1I5IIz3HjvQ3fsFXBhagg8FnVAcj1ad1",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "step-3.7-flash",
                    "messages": [
                        {"role": "user", "content": "Hello, what's 2+2?"}
                    ],
                    "temperature": 0.3
                },
                timeout=30.0
            )
            print(f"Status: {resp.status_code}")
            print(f"Response: {resp.text[:500]}")
        except Exception as e:
            print(f"Error: {e}")

asyncio.run(test_stepfun_api())
