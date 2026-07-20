# 美股盘中每小时持仓复盘自动化

目标：以美东时间常规盘开盘为锚点，在 09:30、10:30、11:30 和 12:30 用 ZA Bank 已核验成交邮件重建当前美股持仓，完成一次可复核的增量分析，将报告保存到本地，并把短结论发到同一个已授权 Gmail。美东夏令时对应上海时间 21:30、22:30、23:30 和次日 00:30；冬令时整体后移一小时。只提供研究建议，不自动下单。

## 每次运行前的硬门禁

1. 工作目录为仓库根目录。先读取本文件、`daily_portfolio_automation_prompt.md`、相关自动化 memory、`data/holdings_commit_manifest.json` 指向的当前 broker generation、可选 analysis overlay、`config/candidate_selection_policy.md`、`data/candidate_watchlist.csv`、`data/candidate_state_log.jsonl` 和 `experience/approved_rules.md`。若环境安装了关联的选股或持仓复盘 skill，可作为补充；未安装时以本仓库规则为准。
2. 从 `data/broker_email_profile.json` 读取 `target_account`，再调用 Gmail `get_profile("me")`。返回账号必须与配置完全一致。不一致时，在搜索邮件、修改本地持仓和发信前停止，输出 `Gmail 目标账号未授权：预期 <配置账号>，实际 <当前账号>，待确认`。
3. 核对美国市场交易日和 `America/New_York` 当前时间。为 09:30、10:30、11:30、12:30 各建立一个半开运行窗口 `[计划点, 计划点+15分钟)`；落入窗口后将本轮归一为对应 `scheduled_slot=YYYY-MM-DD-HHMM-ET`。窗口外、节假日或休市日只记录跳过原因，不生成买卖建议、不发邮件。启动晚几十秒或几分钟不能误判为非计划时点，同一 scheduled_slot 也不得运行两次。
4. `data/broker_email_profile.json.profile_status` 必须为 `CONFIRMED`，且发件人、主题模板、成交状态词和时区已经核验。否则停止并输出 `ZA Bank 邮件模板尚未完成首次核验，待确认`。
5. 生成本次唯一 `run_id`，执行 `<runtime-python> automation_lock.py acquire --name intraday --run-id <run_id> --stale-minutes 75`。返回 `BUSY` 时说明上一轮仍在运行，本轮记录跳过且不读信、不分析、不发信。获得锁后先检查 `data/decision_log.jsonl` 中该 scheduled_slot：已有 `EMAIL_SENT` 时直接幂等跳过；只有未完成发送状态才进入恢复流程。无论成功、失败或门禁停止，退出前都必须用同一 run_id 执行 `release`。

## 邮件和持仓

1. 用已确认发件人白名单搜索 manifest 当前 generation 内 `data/broker_sync_state.json` 指定的最近 7 天重叠窗口，包含 `in:anywhere`，翻完全部分页。读取 message index 时按 message ID 取最新状态；最新为 `DISCOVERED` 必须重试。不得猜测发件地址。
2. 一封邮件多笔执行时拆成多个 event，但不得分别写本地文件。以 `broker_sync_batch_template.json` 为契约，每个 message 对象一次携带 Gmail metadata、content hash、parser version、处理状态、标准化 events 和 quarantine。券商未提供成交编号时，使用 `message_id + 邮件内行号`，并标记为通知代理 ID。
3. 批次必须携带当前 `expected_parent_transaction_id`、同步前后 history ID、扫描起止时间、页数、`pagination_complete` 和终止 page token。只有 `TRADE + FILLED/PARTIALLY_FILLED` 或已核验的 `STOCK_REWARD + CREDITED` 且 `CONFIRMED + affects_position=true` 的事件影响持仓；未知模板使用 `QUARANTINED`。
4. 当前会话若有更晚但不完整的明确持仓校正，用 `apply_chat_holdings_overlay.py` 建立独立 analysis overlay，不写 broker ledger，不冒充 exact anchor。校正后一旦出现已核验持仓事件，分析必须 fail closed，等待新聊天校正或明确清除 overlay。
5. 运行：

```bash
<runtime-python> commit_broker_sync_batch.py --input <本轮已核验批次JSON>
```

脚本在共享 broker-sync 文件锁内合并并校验全部状态，写入 `.broker_commits/<transaction_id>/` 后只原子切换 `.broker_current`。分页不完、终止 token 非空或隔离未解决时提交 `BLOCKED` generation：可留下 DISCOVERED/quarantine 证据，但不合并新 events、不改持仓、不前移成功水位，且分析读取方必须停止方向性建议。

## 分析和独立审阅

1. 持仓重建成功后，将当前可核验的持仓和 SPY 盘中价格观察通过 `append_outcome_price_bar.py` 追加到 `data/outcome_price_bars.csv`。盘中结果条只接受已经结束的完整一分钟：`bar_at` 是一分钟区间起点，`collected_at` 不得早于 `bar_at+1分钟`，不得把仍在形成的分钟写入结果账本。分析前观察只作为当时事实，不是建议可执行参考价。后续观察可作为上一封建议“送达后 1 小时”的候选证据；计算器只接受目标时点前后 15 分钟内最近的完整 bar，并保留真实 `end_price_at`，不得冒充精确 60 分钟。随后运行 `<runtime-python> analyze_portfolio.py` 生成事实与风险底稿。
2. 严格执行 `daily_portfolio_automation_prompt.md` 的市场、个股、SEC、财报、估值、候选池和输出规则。所有价格写明盘前/盘中/盘后、时间和时区。
3. 每次实际派生 5 个只读 subagent：`持仓事实核验员`、`公司事实与SEC核验员`、`基本面与买入逻辑分析师`、`估值与预期分析师`、`风控与反方辩手`。把相同事实包发给它们，由主 agent 汇总分歧并仲裁。无法实际派生时停止为 `当前环境无法实际派生 subagent，待确认`。
4. 动作沿用主提示词允许的枚举，不输出目标价，不承诺收益。若相比上一次报告没有新的成交、公司事实、市场状态或动作变化，明确写 `无新增交易信号，维持上次动作`，避免为了每小时发信而制造交易。
5. 对 `data/candidate_watchlist.csv` 中最多 5 只非 `移出` 候选做增量监控：价格/成交异常、相对 SPY 和行业、SEC、公司公告、重大新闻、下一事件、估值预期、基本面反证及与当前持仓的行业/客户链/因子重叠。原始技术 Top 3 只能作为发现线索，不能直接升级为开仓候选。
6. 严格按 `config/candidate_selection_policy.md` 执行状态迁移。事件必须携带日志重放得到的真实 `previous_state`；`append_candidate_event.py` 会拒绝越级、伪造前态和时间倒序。同步更新 `data/candidate_watchlist.csv` 后必须运行 `analyze_portfolio.py` 的候选一致性校验；CSV 与 JSONL 分叉时停止建议和发信。没有完整事实包时保持 `研究队列` 或 `待确认`，不得用技术分冒充全维选股分。

## 留档和邮件

1. 由 scheduled_slot 确定性生成 `decision_id`，格式 `YYYYMMDD-HHMM-portfolio-<slot短哈希>`；同一 scheduled_slot 重试必须复用完全相同的 decision_id、`DECISION_CREATED` event_id 和邮件幂等标记。
2. 完整报告保存到 `data/reports/intraday/YYYY-MM-DD/portfolio_review_HHMM_<decision_id>.md`。先生成一个 `DECISION_CREATED` JSON 事件。除原有事实和建议字段外，payload 必须写 `decision_kind=PORTFOLIO_REVIEW`、`outcome_contract_version=1`，且 `holdings` 每只都包含 `ticker/action/reference_price/reference_price_at/shares/recommended_exposure`。`recommended_exposure` 是用于标准化反事实的原持仓倍数，0 表示清仓、1 表示原样持有、大于 1 表示加仓，允许范围 0-2；这是即时按参考价调整的统一评估假设，不代表条件必然触发。再运行 `<runtime-python> append_decision_event.py --input <事件JSON> --log data/decision_log.jsonl`。
3. 发信前再次调用 `get_profile("me")`，仍须与配置的 `target_account` 完全一致。定义 `idempotency_marker=portfolio-email:<decision_id>`，先追加唯一 `EMAIL_SEND_INTENT` 事件，payload 至少包含 recipient、subject 和 idempotency_marker。标题为 `[美股盘中复盘 HH:MM] <主要动作>`，正文末尾写入该幂等标记，并按以下顺序：
   - `一句话建议`：今天是否需要交易。
   - `你的可执行选择`：逐只持仓写“主建议 / 满足什么条件再行动 / 当前不要做什么”。这是给用户的研究建议，明确最终决定权在用户。
   - `关键依据与最强反证`：只保留真正改变动作的事实。
   - `新开仓机会`：只有候选状态升级或重大新证据时写具体 ticker；否则固定写 `无新增触发`。
   - `待确认与本地报告`。
4. 发送前若该 decision 已有 `EMAIL_SEND_INTENT` 但没有结果，先在 Gmail Sent 中精确搜索 idempotency_marker：找到时不得重发，直接追加带 Gmail message_id 的 `EMAIL_SENT` 恢复事件；未找到时按 `delivery_unknown` 处理并停止本 slot，不能冒险重复发送。首次发送成功后追加含 message_id 和相同 marker 的 `EMAIL_SENT`；失败时追加含错误和相同 marker 的 `EMAIL_FAILED`。脚本会拒绝孤立发送结果、marker 不一致和同一 decision 的第二次成功发送。
5. `EMAIL_SENT` 成功后，以送达时点向上对齐到下一分钟，等待该一分钟完整结束，为 SPY 和每只持仓追加这一根完成的 `INTRADAY` bar。它是建议结果的唯一可执行参考价；缺失时结果保持 `PENDING_DATA`，不得用 `DECISION_CREATED` 中更早的分析参考价补算。
6. 只有候选升级为 `开仓候选/待确认`、从该状态降级、出现核心逻辑破坏或重大事件时，才额外发送 `[美股候选触发] <ticker> - <状态>` 邮件。候选邮件必须创建独立且确定性的 candidate decision_id，并复用 `DECISION_CREATED -> EMAIL_SEND_INTENT -> EMAIL_SENT/FAILED` outbox 状态机和 Sent marker 恢复流程。正文写清为什么现在、建议动作、等待条件、最强反证、失效条件和待确认项；同一状态和同一证据不得重复发信。
7. 结尾固定包含：`集中持仓可能带来显著回撤，本结论不承诺收益，不替代个人投资判断。最终决定权在用户。`
8. 所有本轮路径完成后执行 `<runtime-python> automation_lock.py release --name intraday --run-id <run_id>`。如果释放失败，记录为运行异常，不重复发送邮件。
