"""English rendering for the portfolio research brief.

The analysis pipeline keeps a single set of internal strings (Chinese), which
the whole ledger and test suite key on. ``--lang en`` therefore translates the
*finished report text* through the reviewed phrase table below instead of
forking every template in ``analyze_portfolio.py``.

The report is fully templated, so this is a closed mapping, not free-form
machine translation: every phrase the report can emit has one reviewed English
rendering here. ``tests/test_report_i18n.py`` runs the deterministic demo brief
through this table and fails if any CJK survives — so adding a new Chinese
fragment to the report without adding its translation breaks the build.
"""

from __future__ import annotations

import re

# zh -> en. Applied longest-key-first so long phrases win over their own
# substrings ("市场数据不足/待确认" before "待确认").
PHRASES: dict[str, str] = {
    # ---- headline and section headers -----------------------------------
    "# 每日美股持仓分析底稿": "# Daily US Portfolio Research Brief",
    "## 0. 今日结论": "## 0. Today's Conclusions",
    "## 1. 市场环境": "## 1. Market Regime",
    "## 2. 持仓事实表": "## 2. Holdings Fact Table",
    "## 3. 量化回撤与尾部风险": "## 3. Drawdown and Tail Risk",
    "## 4. 持续观察候选池": "## 4. Persistent Candidate Pool",
    "## 5. 量化发现队列": "## 5. Quantitative Discovery Queue",
    "## 6. 用户指定观察池": "## 6. User Watchlist",
    "## 7. Subagent 审阅输入": "## 7. Subagent Review Inputs",
    "## 8. 明日关注清单": "## 8. Watch Items for Tomorrow",
    # ---- report meta block ----------------------------------------------
    "- 生成时间：": "- Generated: ",
    "- 美东市场阶段：": "- US market phase: ",
    "；量化表默认只使用已完成日线，常规盘当日未完成 bar 不参与计算。": (
        "; quantitative tables use completed daily bars only — today's"
        " unfinished regular-session bar is excluded."
    ),
    "- 持仓文件：`": "- Holdings file: `",
    "- 持仓分析视图：": "- Holdings analysis view: ",
    "- 截图快照日志：`": "- Snapshot log: `",
    "- 开仓候选池：`": "- Opening candidate universe: `",
    "- 持续观察池：`": "- Persistent watch pool: `",
    "- 说明：本报告是研究辅助，不是投资建议；所有新闻、财报和重大事件需在最终决策前复核来源。": (
        "- Note: this brief is research support, not investment advice; verify"
        " every news item, filing and material event at the source before any"
        " final decision."
    ),
    "盘前": "pre-market",
    "常规盘": "regular session",
    "盘后": "after-hours",
    "休市时段": "market closed",
    # ---- holdings source notes ------------------------------------------
    "DEMO：合成组合 `": "DEMO: synthetic portfolio `",
    "` 与本地确定性行情，非真实持仓与行情": (
        "` with deterministic local prices — not real holdings or market data"
    ),
    "券商派生持仓 `": "Broker-derived holdings `",
    "用户聊天 analysis overlay，correction_id=": (
        "User-chat analysis overlay, correction_id="
    ),
    "；未提供字段不从券商或旧持仓继承": (
        "; fields not provided are never inherited from broker or prior holdings"
    ),
    # ---- conclusions -----------------------------------------------------
    "今日主结论：未触发强动作条件，市场环境为「": (
        'Headline: no strong-action trigger today. Market regime "'
    ),
    "」，默认少动。": '". Default to minimal action.',
    "今日主结论：": "Headline: ",
    "。市场环境为「": '. Market regime "',
    "」，大盘热度为「": '", breadth heat "',
    "」。": '".',
    "持仓数量、市值和仓位只作为事实记录，不作为买卖动作的默认触发器；买卖判断优先看市场环境、个股事实、趋势、估值预期和可验证风险。": (
        "Share counts, market value and weights are recorded facts, never"
        " default trade triggers; decisions weigh market regime, per-name"
        " facts, trend, valuation expectations and verifiable risk first."
    ),
    # ---- market regime ---------------------------------------------------
    "市场结论：": "Market regime: ",
    "。大盘热度：": ". Breadth heat: ",
    "。环境依据：": ". Regime evidence: ",
    "。热度依据：": ". Heat evidence: ",
    "缺少一致的已完成日线：": "missing consistent completed daily bars: ",
    "缺少热度输入：": "missing heat inputs: ",
    "SPY 20日 ": "SPY 20d ",
    "QQQ 20日 ": "QQQ 20d ",
    "半导体相对SPY20日 ": "semis vs SPY 20d ",
    "RSP相对SPY20日 ": "RSP vs SPY 20d ",
    "IWM相对SPY20日 ": "IWM vs SPY 20d ",
    "HYG相对IEF20日 ": "HYG vs IEF 20d ",
    "VIX 5日 ": "VIX 5d ",
    "风险偏好转弱/先控回撤": "Risk-off turning / control drawdown first",
    "风险偏好偏强/顺风": "Risk appetite strong / tailwind",
    "市场分化/中性": "Divergent / neutral",
    "市场数据不足/待确认": "Insufficient market data / unconfirmed",
    "中性偏热": "Neutral-warm",
    "风险规避": "Risk-off",
    "过热": "Overheated",
    "偏冷": "Cool",
    # ---- market table ----------------------------------------------------
    "标的": "Symbol",
    "名称": "Name",
    "用途": "Role",
    "价格": "Price",
    "数据截止": "Data as of",
    "1日": "1d",
    "5日": "5d",
    "20日": "20d",
    "60日": "60d",
    "相对SPY20日": "vs SPY 20d",
    "基准": "benchmark",
    "状态": "State",
    "大盘": "Broad market",
    "科技/成长": "Tech / growth",
    "小盘风险偏好": "Small-cap risk appetite",
    "半导体备选": "Semis (alt)",
    "半导体": "Semiconductors",
    "软件": "Software",
    "市场广度": "Market breadth",
    "信用风险偏好": "Credit risk appetite",
    "中期国债基准": "7-10y Treasury benchmark",
    "波动率": "Volatility",
    "市场": "Market",
    # ---- trend states ----------------------------------------------------
    "趋势转弱/需复核": "Weakening / review",
    "偏热/不宜追高": "Hot / don't chase",
    "超卖/只作风险提示": "Oversold / risk flag only",
    "趋势健康": "Healthy trend",
    "趋势中性": "Neutral trend",
    "数据不足": "Insufficient data",
    # ---- holdings and risk tables ---------------------------------------
    "账户占比(事实)": "Weight (fact)",
    "浮盈亏": "Unrealised P&L",
    "动作底稿": "Drafted action",
    "成本回撤": "Drawdown vs cost",
    "距1年最高收盘": "From 1y high close",
    "60日最大回撤": "60d max drawdown",
    "1年最大回撤": "1y max drawdown",
    "30日年化波动": "30d ann. volatility",
    "95%单日VaR": "95% 1-day VaR",
    "95%单日CVaR": "95% 1-day CVaR",
    "最差单日": "Worst day",
    "回撤指标基于历史日收益和可确认成本字段，不能预测未来；缺少股数、市值或仓位不影响买卖动作判断。": (
        "Drawdown metrics come from historical daily returns and confirmable"
        " cost fields; they do not predict the future. Missing share counts,"
        " market value or weights never drive action judgement."
    ),
    # ---- drafted actions and reasons ------------------------------------
    "现有持仓加仓候选": "Add candidate (existing position)",
    "观望但提高警戒": "Watch with raised alert",
    "继续持有": "Hold",
    "减仓候选": "Trim candidate",
    "卖出审查": "Sell review",
    "未触发强动作条件": "No strong-action trigger",
    "跌破200日均线且60日弱于SPY": "Below 200-day SMA and lagging SPY over 60d",
    "缺少可靠行情数据": "No reliable price data",
    "完成日线历史不足，不能生成趋势动作": (
        "Insufficient completed daily history for a trend action"
    ),
    "市场环境数据不足，暂停新增方向性动作": (
        "Market data insufficient — new directional actions paused"
    ),
    "市场风险偏好转弱且个股20日弱于SPY": (
        "Market risk-off and the name lagging SPY over 20d"
    ),
    "市场顺风且个股趋势健康，需再核验基本面/估值/新闻": (
        "Market tailwind and healthy trend — re-verify"
        " fundamentals/valuation/news first"
    ),
    "浮亏 ": "Unrealised loss ",
    "% 触及用户明确的止损/复核线 ": "% hit the user-defined stop/review line ",
    "浮盈 ": "Unrealised gain ",
    "% 触及用户明确的止盈复核线，且RSI偏热": (
        "% hit the user-defined take-profit review line with hot RSI"
    ),
    "市场环境：": "Market regime: ",
    "。依据：": ". Evidence: ",
    "。核心买入逻辑：": ". Core thesis: ",
    "。逻辑破坏线：": ". Thesis-break line: ",
    # ---- snapshot status -------------------------------------------------
    "本次持仓市值快照跳过：没有可确认持仓。": (
        "Holdings value snapshot skipped: no confirmable holdings."
    ),
    "本次持仓市值快照跳过：持仓价格不属于同一完成交易日。": (
        "Holdings value snapshot skipped: holding prices are not from the same"
        " completed session."
    ),
    "本次持仓市值快照跳过：": "Holdings value snapshot skipped: ",
    "本次持仓市值快照：已写入 ": "Holdings value snapshot: wrote ",
    "本次持仓市值快照：按命令跳过。": "Holdings value snapshot: skipped by flag.",
    "本次持仓市值快照状态：待确认。": "Holdings value snapshot status: unconfirmed.",
    " 股数待确认。": " share count unconfirmed.",
    " 缺少已完成日线价格。": " missing completed daily price.",
    "，共 ": ", ",
    " 只持仓。": " holdings.",
    "持仓市值快照：暂无历史样本，无法计算样本回撤。": (
        "Value snapshot: no history yet; sample drawdown unavailable."
    ),
    "持仓市值快照：样本格式不足，无法计算样本回撤。": (
        "Value snapshot: malformed samples; sample drawdown unavailable."
    ),
    "持仓市值快照：持仓标的或股数发生变化，且缺少现金流调整，停止计算跨期回撤。": (
        "Value snapshot: holdings or share counts changed without cash-flow"
        " adjustment; cross-period drawdown suspended."
    ),
    "持仓市值快照：仅 1 个交易日样本，样本最大回撤待累计。": (
        "Value snapshot: only 1 session sampled; max drawdown accrues later."
    ),
    "持仓市值快照：": "Value snapshot: ",
    " 个交易日样本，": " sessions sampled, ",
    "当前回撤 ": "current drawdown ",
    "样本内最大回撤 ": "in-sample max drawdown ",
    "。该口径未调整买卖现金流，不等同于真实投资收益率。": (
        ". This measure ignores trade cash flows and is not a true return."
    ),
    # ---- candidate pools -------------------------------------------------
    "`研究队列/持续观察/接近触发` 都不是买入建议；只有完成事实核验并升级为 `开仓候选/待确认` 后，才进入邮件机会提示。": (
        "`research queue / watching / near trigger` are never buy"
        " recommendations; only after full fact verification and promotion to"
        " `open candidate / unconfirmed` does a name enter the mail"
        " opportunity note."
    ),
    "以下最多 3 只仅用于发现和后续研究，不能直接写成开仓建议；必须先进入持久候选池并通过完整门禁。": (
        "At most 3 names below are discovery leads for further research, never"
        " direct entry calls; each must first join the persistent candidate"
        " pool and pass the full gate."
    ),
    "观察池仅补充你主动关注的 ticker，不等同于开仓建议。": (
        "The watchlist only adds tickers you actively follow; it is not an"
        " entry recommendation."
    ),
    "未发现真实持仓，今天只能生成开仓候选和模板检查。": (
        "No real holdings found; today's run can only produce opening"
        " candidates and template checks."
    ),
    "研究队列": "research queue",
    "持续观察": "watching",
    "接近触发": "near trigger",
    "开仓候选/待确认": "open candidate / unconfirmed",
    "公司": "Company",
    "主题": "Theme",
    "候选分": "Score",
    "量化理由": "Quant rationale",
    "风险核验": "Risk check",
    "新闻/财报/估值待确认": "News/earnings/valuation unconfirmed",
    "全维分": "Full-dimension score",
    "覆盖率": "Coverage",
    "技术预筛": "Technical pre-screen",
    "技术风险": "Technical risk",
    "无明显技术硬门槛": "No hard technical blocker",
    "下个事件": "Next event",
    "入场条件": "Entry condition",
    "失效条件": "Invalidation",
    "要点": "Key notes",
    # ---- quant score reasons --------------------------------------------
    "高于50日均线": "Above 50-day SMA",
    "高于200日均线": "Above 200-day SMA",
    "60日收益为正": "Positive 60d return",
    "20日相对SPY更强": "Stronger than SPY over 20d",
    "RSI未过热": "RSI not overheated",
    "波动未极端": "Volatility not extreme",
    "一年回撤未极端": "1y drawdown not extreme",
    "RSI过热扣分": "RSI overheated (penalty)",
    "低于200日均线较多": "Well below 200-day SMA",
    "无行情数据": "No price data",
    "RSI偏热": "RSI hot",
    "30日波动过高": "30d volatility too high",
    "一年最大回撤深": "Deep 1y max drawdown",
    "接近一年最高收盘": "Near 1y high close",
    "低于50日均线": "Below 50-day SMA",
    "20日弱于SPY": "Lagging SPY over 20d",
    "完成日线历史不足": "Insufficient completed daily history",
    # ---- subagent review inputs -----------------------------------------
    "本节不是最终多角色结论，只是给自动化运行时实际派生 subagent 的事实输入和审阅问题。最终报告必须整合独立 subagent 输出。": (
        "This section is not the final multi-role conclusion; it is the"
        " factual input and review questions handed to the subagents the"
        " automation actually spawns. The final report must integrate"
        " independent subagent outputs."
    ),
    "### 持仓事实核验员输入": "### Holdings fact-checker input",
    "### 公司事实与SEC核验员输入": "### Company facts and SEC checker input",
    "### 市场环境输入": "### Market regime input",
    "### 基本面与买入逻辑分析师输入": "### Fundamentals and thesis analyst input",
    "### 估值与预期分析师输入": "### Valuation and expectations analyst input",
    "### 风控与反方辩手输入": "### Risk desk and devil's advocate input",
    "### 主 agent 仲裁规则": "### Main-agent arbitration rules",
    "无真实持仓，待填写。": "No real holdings; to be filled.",
    "subagent 必须核对公司公告、SEC 文件、财报日期、重大新闻、盘后/成交量/行业事实，并标注来源和日期；不可确认的内容写待确认。": (
        "The subagent must check company announcements, SEC filings, earnings"
        " dates, major news, and after-hours/volume/sector facts, citing"
        " source and date; anything unverifiable is written as unconfirmed."
    ),
    "。核心依据：": ". Core evidence: ",
    "。subagent 必须判断大盘、科技/成长、小盘、市场广度、信用风险偏好、半导体、软件和 VIX 是否支持今天的动作。": (
        ". The subagent must judge whether broad market, tech/growth, small"
        " caps, breadth, credit risk appetite, semis, software and VIX support"
        " today's actions."
    ),
    "subagent 必须基于当天可确认的 10-K/10-Q/8-K/6-K、财报、监管、诉讼、管理层或核心客户变化，判断原始买入逻辑是增强、未变、走弱、被破坏还是信息不足。": (
        "Based on what is confirmable today (10-K/10-Q/8-K/6-K, earnings,"
        " regulation, litigation, management or key-customer changes), the"
        " subagent must judge whether the original thesis is strengthened,"
        " unchanged, weakening, broken, or under-informed."
    ),
    "subagent 必须判断估值、市场预期、财报前预期差和价格是否已经提前反映好消息；脚本底稿不把估值倍数作为机械交易信号。": (
        "The subagent must judge valuation, market expectations, pre-earnings"
        " expectation gaps, and whether price already reflects the good news;"
        " this draft never uses valuation multiples as mechanical trade"
        " signals."
    ),
    "subagent 必须检查止损、回撤、波动、市场风险偏好、板块拥挤、财报前风险、一票否决项，并回答：如果这笔投资是错的，最可能错在哪里？今天是否出现支持反方观点的新证据？有没有比继续持有更保守的选择？持仓数量/仓位字段不得作为默认买卖理由。": (
        "The subagent must check stops, drawdown, volatility, market risk"
        " appetite, sector crowding, pre-earnings risk and veto items, and"
        " answer: if this investment is wrong, where is it most likely wrong?"
        " Did new evidence appear today supporting the bear case? Is there a"
        " more conservative option than holding? Share-count and weight fields"
        " must never serve as default trade reasons."
    ),
    "市场环境、个股事实、估值预期和风控证据共同仲裁；触及止损/复核线、市场风险偏好恶化且个股走弱、或出现已核验经营性利空时，风控结论优先于收益想象。持仓数量、市值和仓位只记录，不作为动作触发器。": (
        "Market regime, per-name facts, valuation expectations and risk"
        " evidence arbitrate together; when a stop or review line is hit, when"
        " risk appetite deteriorates while the name weakens, or when verified"
        " operating negatives appear, the risk conclusion outranks return"
        " imagination. Share counts, market value and weights are recorded,"
        " never triggers."
    ),
    # ---- tomorrow's watch items -----------------------------------------
    "- 不要求手工补齐 CSV；用户聊天没有提供的持仓数量、市值、仓位字段继续写待确认，但不影响买卖动作判断。": (
        "- No manual CSV backfill is required; share counts, market value and"
        " weights absent from user chat stay unconfirmed and never block"
        " action judgement."
    ),
    "- 每天必须复核 SPY、QQQ、IWM、SMH/SOXX、IGV、RSP/SPY、HYG/IEF、VIX，判断大盘、科技、市场广度、信用风险偏好、半导体、软件和波动率是否支持动作。": (
        "- Re-check SPY, QQQ, IWM, SMH/SOXX, IGV, RSP/SPY, HYG/IEF and VIX"
        " daily to judge whether broad market, tech, breadth, credit risk"
        " appetite, semis, software and volatility support the actions."
    ),
    "- 每日量化发现最多 3 只研究线索；只有持久候选池完成全维核验并发生状态升级时，才可写开仓候选/待确认。": (
        "- Daily quantitative discovery yields at most 3 research leads; write"
        " `open candidate / unconfirmed` only after the persistent pool"
        " completes full verification with a state upgrade."
    ),
    "- 对任何现有持仓的加仓想法补充最新财报、SEC 文件、估值和新闻来源。": (
        "- For any add idea on an existing position, attach the latest"
        " earnings, SEC filings, valuation and news sources."
    ),
    "- 若角色分歧大或证据不足，默认观望。": (
        "- When roles disagree materially or evidence is thin, default to"
        " standing aside."
    ),
    # ---- generic fills (keep last so longer phrases win) -----------------
    "实际 / 持有 / 建议": "actual / hold / advice",
    "待确认": "unconfirmed",
    "未填写": "not filled",
    "无\n": "None\n",
}

_ORDERED = sorted(PHRASES.items(), key=lambda kv: len(kv[0]), reverse=True)

# CJK punctuation that may survive between translated fragments.
_PUNCT = {
    "：": ": ",
    "，": ", ",
    "。": ". ",
    "；": "; ",
    "、": ", ",
    "（": " (",
    "）": ") ",
    "「": ' "',
    "」": '"',
    "％": "%",
    "｜": "|",
}

_CJK = re.compile(r"[　-〿一-鿿豈-﫿＀-￯]")


def contains_cjk(text: str) -> bool:
    return bool(_CJK.search(text))


def translate_report(text: str) -> str:
    """Render a finished Chinese brief in English via the phrase table."""
    for zh, en in _ORDERED:
        text = text.replace(zh, en)
    for zh, en in _PUNCT.items():
        text = text.replace(zh, en)
    # Punctuation expansion can leave doubled spaces or trailing blanks.
    text = re.sub(r"(?<=\S)  +(?=\S)", " ", text)
    text = re.sub(r" +(\n|$)", r"\1", text)
    return text
