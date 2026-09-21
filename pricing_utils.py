from __future__ import annotations


def _number(value: object, digits: int = 4) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    if number == 0:
        return "0"
    if abs(number) >= 100:
        return f"{number:.2f}"
    if abs(number) >= 1:
        return f"{number:.{digits}f}".rstrip("0").rstrip(".")
    return f"{number:.6f}".rstrip("0").rstrip(".")


def format_pricing(pricing: dict | None) -> str:
    if not pricing:
        return "查询中..."

    pricing_block = pricing.get("pricing") if isinstance(pricing.get("pricing"), dict) else pricing
    effective = pricing_block.get("effective_rates") or pricing_block.get("rates")
    if isinstance(effective, dict):
        input_price = effective.get("input")
        output_price = effective.get("output")
        cached_price = effective.get("cached_input")
        parts = []
        if input_price is not None:
            parts.append(f"输入 ${_number(input_price)}/百万Token")
        if output_price is not None:
            parts.append(f"输出 ${_number(output_price)}/百万Token")
        if cached_price is not None:
            parts.append(f"缓存 ${_number(cached_price)}/百万Token")
        limits = pricing_block.get("limits")
        if isinstance(limits, dict):
            max_input = limits.get("max_input_tokens")
            max_output = limits.get("max_output_tokens")
            if max_input:
                parts.append(f"输入上限 {int(max_input):,} Token")
            if max_output:
                parts.append(f"输出上限 {int(max_output):,} Token")
        return " · ".join(parts) if parts else "暂无展示价"

    resolution_prices = pricing.get("resolution_prices")
    if isinstance(resolution_prices, dict) and resolution_prices:
        billing = str(pricing.get("billing_type") or "")
        suffix = "/秒" if billing == "per_second" else "/张"
        parts = [f"{resolution} ${_number(price)}{suffix}" for resolution, price in resolution_prices.items()]
        return " · ".join(parts)

    model_price = pricing.get("model_price")
    if model_price is not None:
        return f"${_number(model_price)}/次"

    return "暂无展示价"


def format_usage(usage: dict | None) -> str:
    if not usage:
        return "暂无用量"
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    total = prompt_tokens + completion_tokens
    if total <= 0:
        return "该模型按次/秒计费，无Token用量"
    return f"输入 {prompt_tokens:,} · 输出 {completion_tokens:,} · 合计 {total:,} Token"


def format_billing(usage: dict | None) -> str:
    if not usage:
        return "暂无账单"
    amount = _number(usage.get("amount_usd"))
    credits = _number(usage.get("credits"))
    requests = int(usage.get("requests") or 0)
    return f"${amount} · {credits} 积分 · {requests} 次"


def format_balance(balance: dict | None) -> str:
    if not balance:
        return "余额：未查询"
    if balance.get("unlimited_quota"):
        return f"余额：无限额度 · 已用 ${_number(balance.get('used_balance'))}"
    return (
        f"余额：${_number(balance.get('remain_balance'))} · "
        f"已用：${_number(balance.get('used_balance'))}"
    )
