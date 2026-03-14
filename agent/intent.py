import logging
from typing import Any

from openai import AsyncOpenAI

from config import INTENT_MODEL

logger = logging.getLogger(__name__)

INTENT_PROMPT = """判断以下用户消息是否需要深度思考和推理。

需要深度思考的情况：
1. 多步推理问题（为什么、如何分析、比较）
2. 复杂计算或逻辑推导
3. 需要深度分析的问题
4. 开放性问题需要阐述观点

不需要深度思考的情况：
1. 简单事实问答
2. 信息查询
3. 简单指令或任务
4. 日常闲聊

用户消息：{user_message}

请只回答 YES 或 NO，不要解释。"""


async def should_enable_thinking(
    content: str | list[dict[str, Any]], client: AsyncOpenAI
) -> bool:
    """
    判断用户消息是否需要启用深度思考

    Args:
        content: 用户消息内容（文本或多模态内容）
        client: AsyncOpenAI客户端实例

    Returns:
        True 表示需要深度思考，False 表示不需要
    """
    try:
        # 提取文本内容用于判断
        if isinstance(content, str):
            text_content = content
        elif isinstance(content, list):
            # 多模态消息，提取文本部分
            text_parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(item.get("text", ""))
            text_content = " ".join(text_parts)
        else:
            text_content = str(content)

        # 如果内容为空或太短，不需要深度思考
        if not text_content or len(text_content.strip()) < 5:
            return False

        # 调用意图判断模型
        prompt = INTENT_PROMPT.format(user_message=text_content[:500])

        response = await client.chat.completions.create(
            model=INTENT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=10,
            temperature=0,
        )

        result = response.choices[0].message.content.strip().upper()
        logger.info(f"[Intent] judgment result: {result}")

        # 解析结果
        return "YES" in result

    except Exception as e:
        logger.error(f"[Intent] error during intent judgment: {e}", exc_info=True)
        # 出错时默认不启用深度思考（安全降级）
        return False
