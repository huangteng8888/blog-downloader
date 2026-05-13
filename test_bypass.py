#!/usr/bin/env python3
import asyncio, aiohttp, json, os

API_KEY = os.environ.get("MINIMAX_API_KEY", "")
API_URL = "https://api.minimax.chat/anthropic/v1"

async def test():
    url = f"{API_URL}/messages"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    payload = {
        "model": "MiniMax-M2.7",
        "messages": [
            {"role": "system", "content": "输出纯markdown。"},
            {"role": "user", "content": "均线和MACD如何判断力度衰竭？缠论思路，400字。"}
        ],
        "max_tokens": 8192,
        "extra": {"beta": {"requests_full": True, "thinking_bypass": True}},
    }

    print(f"Payload extra: {payload['extra']}")

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers,
                               timeout=aiohttp.ClientTimeout(total=120)) as resp:
            print(f"Status: {resp.status}")
            data = await resp.json()
            print(f"Content blocks: {len(data.get('content', []))}")
            for b in data.get('content', []):
                t = b.get('type')
                txt = b.get('text', '')[:100] if b.get('text') else '[empty]'
                print(f"  [{t}] {txt}")

asyncio.run(test())
