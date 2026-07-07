#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Send all personal strategies to DeepSeek for optimization analysis."""

import json
import os
import re
import sys
import yaml
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def load_dotenv() -> None:
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        return
    with open(env_file, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"^(\w+)\s*=\s*(.+)$", line)
            if m:
                key, value = m.group(1), m.group(2).strip()
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                elif value.startswith("'") and value.endswith("'"):
                    value = value[1:-1]
                os.environ.setdefault(key, value)


load_dotenv()

from openai import OpenAI

STRATEGIES_DIR = PROJECT_ROOT / "strategies"


def load_all_strategies() -> dict:
    strategies = {}
    for f in sorted(STRATEGIES_DIR.glob("*.yaml")):
        with open(f, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
            name = data.get("name", f.stem)
            strategies[name] = {
                "display_name": data.get("display_name", ""),
                "category": data.get("category", ""),
                "description": data.get("description", ""),
                "instructions": data.get("instructions", ""),
            }
    return strategies


def build_prompt(strategies: dict) -> str:
    profile_path = PROJECT_ROOT / "config" / "local_strategy_profile.json"
    with open(profile_path, encoding="utf-8") as fh:
        profile = json.load(fh)

    strategy_text = ""
    for i, (name, s) in enumerate(strategies.items(), 1):
        instructions = s["instructions"].strip()
        if len(instructions) > 1500:
            instructions = instructions[:1500] + "\n... (truncated)"
        strategy_text += f"""
### {i}. {s['display_name']} (`{name}`) | 分类: {s['category']}
**描述**: {s['description']}
**指令摘要**:
{instructions}
---
"""

    prompt = f"""你是一位专业量化策略顾问。以下是用户维护的 A 股选股策略文件。

当前每日运行的复盘管线包括 5 条信号：
- earnings（业绩超预期）
- hundred_day_high（百日新高突破）
- trend_leader（趋势龙头统一骨架）
- daily_slow_rise（日线30-45度慢涨）
- long_base_release（长横盘后释放）

下面是用户剩下的 7 个 Agent 技能策略（YAML 格式）：

{strategy_text}

请做三件事：

1. **逐个评估**：每个策略的设计是否合理、有无逻辑漏洞、哪里可以加强
2. **找出重叠**：策略之间有没有覆盖范围重叠、信号打架的地方
3. **优化建议**：给出具体的优化方案（合并、修补、增强）

要求：
- 用中文回答
- 不要泛泛而谈，给具体可执行的建议
- 重点关注：哪些策略的 instructions 写得太松散，导致 AI 分析时容易出偏差
"""

    return prompt


def main():
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("[ERROR] DEEPSEEK_API_KEY not set")
        sys.exit(1)

    print("Loading strategies...")
    strategies = load_all_strategies()
    print(f"  Loaded {len(strategies)} strategies: {list(strategies.keys())}")

    prompt = build_prompt(strategies)
    print(f"\nPrompt length: {len(prompt)} chars")
    print("\n" + "=" * 60)
    print("Sending to DeepSeek for analysis, please wait...")
    print("=" * 60 + "\n")

    client = OpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com",
    )

    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {
                "role": "system",
                "content": "你是专业量化策略顾问，擅长分析A股交易策略的优劣并给出优化方案。",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        max_tokens=4096,
    )

    result = response.choices[0].message.content

    output_path = PROJECT_ROOT / "data" / "deepseek_strategy_analysis.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write("# DeepSeek 策略优化分析\n\n")
        fh.write(result)

    print(result)
    print(f"\n[OK] Result saved to: {output_path}")


if __name__ == "__main__":
    main()
