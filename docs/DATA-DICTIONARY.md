# 字段与业务定义

输入编码 UTF-8，允许 BOM。首行必须匹配样例列顺序。记录键和业务组保留原始字符串。`kind` 允许 external, required, equivalence, authorization。说明列 `note` 可以跨行，限制 200 字。

业务字段按 `external_course`、`local_course`、`credits`、`priority` 排列。最终值单位见阶段结果。数字接受非负整数、全角数字和完整千分位。空串、短横线、N/A、NULL 与未知为缺值。无关字段用 0 占位。

权重为课程互认学分。最多 12 门校内课程。外校课与校内课均只有一个名额，先最大化学分，再比较审批等级之和，最后按课程键稳定择一。最低学分由培养方案给出。

`incoming.csv` 包含四个业务组，第二组提供不同边界，第三组触发业务拒收，第四组需要核对。另含重复表头、冲突记录、重复回放与错误列数。`ready.csv` 含可以签发的两个组。

# 补录签发扩展

- `application_id`（申请编号）：同一申请跨批次归集的键；接口未传时取 CSV 中唯一的 `case` 业务组号。
- `receipt` / `receipt_id` / `idempotency_key`（回执键）：同一申请下唯一；签发成功后回执中断，用同一键重试返回同一结论，不重复写抵扣。
- `claims`（已签发配对/抵扣）：`(application_id, external_course)` 与 `(application_id, local_course)` 各有唯一索引，因此同一外校课或校内课在同一申请的跨批次中只能抵扣一次。
- 补录结论：`ISSUED`、`ISSUABLE`、`CREDIT_SHORTFALL`、`RECALCULATE`（`reason=COURSE_CLAIMED`）；配对明细在 `pairs`，证据沿用 `evidence.matched/unmet`，`switched=true` 表示发生了换组配对。
- `sample_data/supplement-reroute.csv` 需先签发 `ready.csv`（或直接先提交一次该申请的基础批次）：其原方案使用已锁定的 1/2 课程，剩余的 `3→3` 配对为 5 学分，正好达到该批 `authorization=5` 的最低学分。
