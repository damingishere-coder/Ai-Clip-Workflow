"""分析失败的统一用户说明，不改变完整性门禁或重试授权。"""


def incomplete_analysis_message(meta: dict) -> str:
    labels = {"recall": "召回", "expansion": "片段扩展", "global_judge": "最终评审"}
    failures = meta.get("failed_stages") or []
    details = []
    for failure in failures:
        if not isinstance(failure, dict):
            continue
        label = labels.get(failure.get("stage"), "分析单元")
        error = str(failure.get("message") or "")
        if any(word in error for word in ("invalid_response_json", "invalid_response_schema", "格式错误", "合法 JSON")):
            label += "格式错误"
        elif "时间范围" in error or "起止时间" in error:
            label += "时间范围校验失败"
        elif "source_id" in error or "候选" in error:
            label += "候选编号校验失败"
        else:
            label += "未完成"
        if label not in details:
            details.append(label)
    reason = "、".join(details) or "AI 分析存在未完成单元"
    completed, expected = int(meta.get("completed_units") or 0), int(meta.get("expected_units") or 0)
    coverage = float(meta.get("coverage_percent") or 0)
    return (
        f"{reason}；已完成 {completed}/{expected} 个单元，覆盖率 {coverage:.2f}%。"
        "成功结果已保存；输入一致时复用已校验的成功单元，仅补跑失败或输入变化的单元。"
        "请通过重试入口核对并确认结果不确定的请求；补齐前不会生成切片，也不会进入自动切片或发送中心。"
    )
