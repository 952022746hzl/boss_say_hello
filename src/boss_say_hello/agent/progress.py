"""User-facing names for bounded agent tools."""

TOOL_LABELS = {
    "open_preferences": "打开岗位页",
    "inspect_preferences": "读取标签与筛选",
    "preference_action": "操作求职标签控件",
    "verify_preferences": "核验筛选条件",
    "list_jobs": "读取岗位列表",
    "read_job": "读取岗位详情",
    "search_company": "联网查询公司",
    "assess_job": "核验评分与外包结论",
    "draft_greeting": "生成招呼语",
    "send_greeting": "检查并发送招呼语",
    "skip_job": "跳过岗位",
    "finish": "结束本轮",
}

PHASES = {
    "open_preferences": "browser_action",
    "inspect_preferences": "page_read",
    "preference_action": "browser_action",
    "verify_preferences": "verify",
    "list_jobs": "page_read",
    "read_job": "page_read",
    "search_company": "company_search",
    "assess_job": "verify",
    "draft_greeting": "model_wait",
    "send_greeting": "delivery",
    "skip_job": "record",
    "finish": "record",
}

PHASE_LABELS = {
    "rate_limit_wait": "模型限流，正在等待重试",
    "retry_wait": "重试已发起，正在等待服务响应",
    "model_wait": "等待模型响应",
    "browser_action": "正在操作页面",
    "page_read": "正在读取页面",
    "verify": "正在核验结果",
    "company_search": "正在联网查询公司",
    "delivery": "正在处理发送",
    "record": "正在保存记录",
}
