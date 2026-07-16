# 美股收盘后日周月复盘自动化

目标：每个美国交易日收盘并等待数据稳定后，评估当天用户真实成交与系统建议的质量，更新已成熟的历史结果，沉淀候选经验；周五美股交易日追加周复盘，月末最后一个美股交易日追加月复盘。

## 门禁和数据边界

1. 先读取本文件、`data/holdings_commit_manifest.json` 指向的不可变 broker generation、`data/decision_log.jsonl`、`data/outcome_price_bars.csv`、`data/decision_outcomes.csv`、`config/candidate_selection_policy.md`、`data/candidate_watchlist.csv`、`data/candidate_state_log.jsonl`、`experience/approved_rules.md` 和相关自动化 memory。若环境安装了关联的持仓复盘 skill，可作为补充；未安装时以本仓库规则为准。
2. 从 `data/broker_email_profile.json` 读取 `target_account`，Gmail `get_profile("me")` 必须与其完全一致。账号不符时停止，不读邮件、不改持仓、不发信。
3. 核对最新已完成的美国常规交易日。交易所休市或数据尚未形成一致收盘快照时，延后为待确认，不用盘中价冒充收盘价。
4. 生成唯一 run_id 并执行 `.venv/bin/python automation_lock.py acquire --name post-close --run-id <run_id> --stale-minutes 75`。返回 `BUSY` 时跳过；获得锁后必须在所有成功或失败出口执行同一 run_id 的 `release`。
5. 重扫最近 30 天已确认发件人的 ZA Bank 邮件并翻完分页。按 `broker_sync_batch_template.json` 生成同一批次，只调用 `.venv/bin/python commit_broker_sync_batch.py --input <batch.json>`；不得分别写 message index、events、quarantine 或 sync state。返回 `BLOCKED` 时成功水位不前移，不得评价用户操作。

## 复盘计算

1. 收盘数据稳定后，为持仓和 SPY 逐只追加 `DAILY_CLOSE` 价格条；`bar_at` 必须精确等于 XNYS 当日收盘时点，`session_date`、来源和采集时间必须留档。然后用显式知识截止时间运行：

```bash
.venv/bin/python calculate_decision_outcomes.py --as-of <ISO-8601时间及时区>
```

计算器用 XNYS 正式交易日历从邮件实际送达所在的决策交易日派生收盘、1/5/20 日窗口，不使用参考价日期，也不用现有 SPY bar 列表冒充日历。目标日 SPY 或个股 bar 缺失时保持 `PENDING_DATA`，不得顺延到下一根。
2. 从 `data/decision_log.jsonl` 的 `DECISION_CREATED`、`EMAIL_SEND_INTENT`、`EMAIL_SENT/FAILED` 事件重建当时决策状态。只有在 `--as-of` 之前可见的决策、送达事件、成交通知、解析时点和价格条能进入计算。未送达建议标为 `NOT_DELIVERED`；未成熟窗口保持 `PENDING`，禁止使用未来数据。
3. 对每个 decision_id 区分：建议发出前已下单、建议后成交、无成交、因果待确认。除非用户明确说明，不得把时间先后写成“采纳建议”。
4. 比较实际组合、原样持有基准和标准化建议反事实。建议反事实以当时留档的 `recommended_exposure` 为准，统一假设在 reference price 即时调整并扣除变更 exposure 的成本；这只是可比基准，不得写成条件已真实触发。成交时间仅为通知代理或手续费缺失时标为 `MATURED_PARTIAL`。
5. 日复盘只评价当日流程、数据质量、新建议与已成熟结果，不因单日输赢改规则。
6. 给用户的操作复盘必须分开写：`当时我的建议`、`用户实际成交`、`原样持有基准`、`现在看错在哪里`、`下一次用户决策可改进的一条具体习惯`。不得只用结果倒推当时决定对错。
7. 每个完成交易日对持久候选池做一次全维审阅，更新覆盖率、六维分数、下一事件、最强反证、入场条件和失效条件；按策略追加 `CANDIDATE_REVIEWED/STATE_CHANGED` 事件。
8. 若最新已完成交易日是周五，追加周复盘；若是该月最后一个交易日，追加月复盘。买卖阈值、评分权重或状态门槛至少需要 20 个独立信号、覆盖多个市场环境、留出期风险调整结果改善且最大回撤不恶化，才可生成 skill 候选 diff。

## 经验和 skill 变更

1. 将候选经验追加到 `experience/candidate_rules.md`，每条包含日期、问题、证据、反证、样本数、适用边界、验证结果和建议 diff。
2. 不得由一天或一周的盈亏直接改写选股规则、持仓规则或 `experience/approved_rules.md`。
3. 月度验证通过时，只生成 `skill_proposals/YYYY-MM-DD_<slug>.md`，包含目标 skill、旧规则、新规则、证据窗口、回测方法、结果和回滚方式；邮件通知用户审阅。用户确认后才更新生效 skill。
4. 所有复盘保存到 `data/reports/review/daily/`、`data/reports/review/weekly/` 或 `data/reports/review/monthly/`，并记录所用提示词、策略和已生效规则的内容哈希。

## 邮件

发信前再次核对 Gmail 身份。向配置的 `target_account` 自发短摘要，标题按实际包含层级使用 `[美股收盘复盘-日]`、`[美股收盘复盘-日周]` 或 `[美股收盘复盘-日周月]`。正文包含：今日建议质量、用户成交事实、实际/持有/建议反事实对比、用户下一次决策改进、候选池状态变化与新开仓机会、已成熟结果、主要错误、保留/候选经验、待确认项和本地报告路径。

结尾固定包含：`集中持仓可能带来显著回撤，本结论不承诺收益，不替代个人投资判断。`

最后执行 `.venv/bin/python automation_lock.py release --name post-close --run-id <run_id>`；释放失败要留痕，不重复发信。
