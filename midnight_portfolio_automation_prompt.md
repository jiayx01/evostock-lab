# 美股每日午夜持仓复盘与学习自动化

目标：上海时间每天 00:00 唤醒一次。在美国常规交易日且 XNYS 正处于常规交易时段时，先补算截至当前已经成熟的历史结果并召回可用经验，再重建持仓、完成一次可复核的盘中判断，保存完整报告并向已核验 Gmail 只发送一封摘要。休市日或不在运行窗口时静默记录跳过。只提供研究建议，不自动下单。

## 规则优先级

1. 本文件负责调度、单次运行边界、学习顺序和持久化记忆契约。
2. `intraday_portfolio_automation_prompt.md` 负责 Gmail、券商邮件、原子持仓 generation、分析、subagent、候选池和 outbox 细节；其中每小时计划点、每小时重复邮件和旧报告路径不再适用。
3. `daily_portfolio_automation_prompt.md` 负责市场、个股、SEC、财报、估值、风险和输出口径。
4. `post_close_portfolio_automation_prompt.md` 负责无未来数据结果计算、日周月复盘和规则晋级门禁；本轮不单独发送收盘复盘邮件。
5. `portfolio_memory_strategy.md` 负责长期记忆的事实层、经验层、规则层和召回边界。发生冲突时按以上顺序执行。

## 调度与硬门禁

1. 先完整读取本文件、上述四份规则、相关自动化 memory、`data/holdings_commit_manifest.json` 指向的不可变 broker generation、可选 analysis overlay、候选池及状态日志、`data/decision_log.jsonl`、`data/decision_outcomes.csv`、`experience/candidate_rules.md` 和 `experience/approved_rules.md`。
2. 以 `Asia/Shanghai` 当前时间建立半开窗口 `[00:00, 00:15)`。窗口内归一为 `scheduled_slot=YYYY-MM-DD-0000-CST`；窗口外只记录跳过，不读 Gmail、不改持仓、不分析、不发信。
3. 将当前时间转换为 `America/New_York`，用正式 XNYS 日历确认该 ET 日期为交易日，且当前时间位于该日官方常规开盘和收盘之间。这样自动覆盖夏令时、冬令时、休市和提前收盘。条件不满足时静默记录跳过。
4. 从 `data/broker_email_profile.json` 读取 `target_account`，调用 Gmail `get_profile("me")`。结果必须精确等于配置账号，且 `profile_status=CONFIRMED`；否则在搜索邮件、改持仓、输出方向性建议和发信前停止。
5. 生成唯一 `run_id`，执行 `.venv/bin/python automation_lock.py acquire --name daily-midnight --run-id <run_id> --stale-minutes 75`。`BUSY` 时跳过。同一 `scheduled_slot` 已有 `EMAIL_SENT` 时幂等跳过；有 intent 无结果时按 Sent marker 恢复规则处理。所有出口都必须用同一 run_id 释放锁。

## 先学习，再判断

1. 识别 `as_of` 之前已经完成、但结果账本尚未覆盖的 XNYS 交易日。只为实际需要的 SPY、历史决策 ticker 和候选 ticker 补充缺失的 `DAILY_CLOSE` 或合格的 1 小时观察；`bar_at`、`session_date`、`source` 和 `collected_at` 必须真实留档。
2. 运行 `.venv/bin/python calculate_decision_outcomes.py --as-of <ISO-8601时间及时区>`。只使用知识截止点之前可见的决策、邮件送达、成交、解析和行情；未成熟窗口保持 `PENDING`，不得使用未来数据。
3. 从已成熟结果中召回与当前 ticker、证券结构、动作、市场热度、行业环境和理由因子最相近的历史决策。召回必须显示样本数、结果窗口和数据缺口；相似案例只能作为证据，不能覆盖当前事实。
4. 自动化 memory 只保存运行游标、最近成功处理的 session/decision、未解决门禁和文件路径，不保存未经验证的投资观点。候选经验写入 `experience/candidate_rules.md`；只有 `experience/approved_rules.md` 中的规则可以直接影响当次生产动作。
5. 日复盘只记录事实质量、错误类型和待验证经验。周复盘按市场环境、动作和因子分组；月复盘才允许形成 skill proposal。买卖阈值或权重至少需要 20 个独立信号、多个市场环境、时间顺序留出验证、交易成本后风险调整结果改善且最大回撤不恶化，并经用户确认后，才可进入生效规则。

## 当次持仓判断

1. 按已确认发件人白名单重扫最近 7 天 Gmail，翻完全部分页，并通过 `commit_broker_sync_batch.py` 原子提交 message index、events、quarantine、sync state、holdings 和 audit。任何身份、分页、解析、隔离或 manifest 门禁失败时停止方向性建议。
2. 运行 `.venv/bin/python analyze_portfolio.py`，补充当时可见的市场、行业、持仓、候选、SEC、财报、估值和新闻事实。所有行情标注时间、时区和市场阶段。
3. 实际派生 5 个只读 subagent：持仓事实核验员、公司事实与SEC核验员、基本面与买入逻辑分析师、估值与预期分析师、风控与反方辩手。使用同一事实包，完成两组交叉质询后由主 agent 仲裁。
4. 历史经验与当前事实冲突时，以当前已核验事实为准。没有新成交、公司事实、市场状态、成熟反证或动作变化时写 `无新增交易信号，维持上次动作`，不得为了每日邮件制造交易。

## 决策记忆契约

每个 `DECISION_CREATED.payload` 除既有 outcome contract 外，必须新增：

- `decision_context_version=1`、`knowledge_cutoff`、`market_stage` 和 `scheduled_slot`。
- `episode_id`、`independent_signal` 和 `episode_continuation_reason`。同 ticker、同 thesis、同主要因子且没有实质新证据的每日重复判断必须复用 episode_id，并标记 `independent_signal=false`，不能冒充新的独立样本。
- `market_regime`：至少含市场热度、RSP/SPY 广度、HYG/IEF 信用风险偏好、VIX 状态和持仓行业环境。
- `reason_factors`：每项包含稳定 `factor_id`、方向、强度、当时观察值或事实、解释、来源、来源时点和置信度。
- `strongest_counterevidence`、`action_conditions`、`prohibited_actions` 和 `uncertainties`。
- `rule_bundle`：本次使用的提示词、skill、候选策略和已生效规则的版本或 SHA-256。
- `retrieved_memory`：实际召回的历史 decision_id、成熟窗口、相似依据和样本限制；没有合格案例时写空列表。
- `report_sha256`：完整报告的 SHA-256，用于确认结构化事件与人类可读报告属于同一版本。

用户实际操作只从已核验 broker events 或用户明确聊天确认记录。时间相邻只能写“成交发生在建议之后”；只有用户明确确认因果时才追加 `USER_CAUSALITY_CONFIRMED`。

## 留档与单封邮件

1. 由 `scheduled_slot` 确定性生成 `decision_id=YYYYMMDD-0000-portfolio-<slot短哈希>`。完整报告保存到 `data/reports/daily_midnight/YYYY-MM-DD/portfolio_review_0000_<decision_id>.md`。
2. 一轮只允许一封持仓复盘邮件。标题为 `[美股每日复盘 00:00] <主要动作>`；正文依次包含一句话建议、逐只可执行选择、改变动作的事实、最强反证、历史经验与成熟结果摘要、新开仓机会、待确认项和本地报告路径。
3. 周/月复盘、候选状态变化和 skill proposal 摘要并入同一封邮件，不另发重复邮件。只有投递恢复流程允许对同一 decision 继续处理，但必须复用唯一 idempotency marker，禁止重复成功发送。
4. 首次成功追加 `EMAIL_SENT` 后，以实际送达时点向上对齐到下一分钟，等待该一分钟完整结束，再为 SPY 和每只持仓追加这一根完成的 `INTRADAY` bar。`bar_at` 是一分钟区间起点，`collected_at` 必须不早于 `bar_at+1分钟`。结果计算器只接受这根精确送达后 bar 作为可执行参考；缺失时保持 `PENDING_DATA`，不得回退到送达前分析参考价，也不得进入规则晋级样本。
5. 结尾固定包含：`集中持仓可能带来显著回撤，本结论不承诺收益，不替代个人投资判断。最终决定权在用户。`
6. 无论成功、失败或门禁停止，更新自动化 memory 的运行时间、结果、已处理游标和待确认项，并释放 `daily-midnight` 锁。不得自动下单。
