# 职校学分互认台规则登记

- 规模：中型

## 功能模块

| 模块 | 动作 | 接口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 样例采信 | 预览并定位坏行 | `POST /api/stages/intake` | `app/intake/service.py` | `tests/test_intake.py` |
| M2 课程适配 | 检查领域约束 | `POST /api/stages/prepare` | `app/modules/prepare.py` | `tests/test_prepare.py` |
| M3 互认分配 | 查看业务计算结果 | `POST /api/stages/calculate` | `app/modules/calculate.py` | `tests/test_calculate.py` |
| M4 培养要求核验 | 查看签收判定 | `POST /api/stages/policy` | `app/modules/policy.py` | `tests/test_policy.py` |
| M5 运行对账 | 对照输入、采信和隔离行 | `POST /api/stages/balance` | `app/modules/reconcile.py` | `tests/test_reconcile.py` |
| M6 批次复核 | 执行批次并签发或暂缓 | `POST /api/runs、PATCH /api/runs/<id>` | `app/review/service.py` | `tests/test_review.py` |
| M7 证据交付 | 下载复核清单 | `GET /api/runs/<id>/evidence` | `app/evidence/render.py` | `tests/test_evidence.py` |
| M8 补录签发 | 冻结已签发配对、跨批次唯一抵扣、撞课换组、并发裁决、回执幂等、结论查询 | `POST /api/applications/<id>/supplements`、`GET /api/applications/<id>` | `app/supplement/service.py`、`app/supplement/planner.py`、`app/supplement/web.py`、`app/review/store.py` | `tests/test_zz_agent_supplement_rules.py` |

## 输入陷阱

| 编号 | 规则 | 处理 | 测试 |
|---|---|---|---|
| TRAP-02 | 去除 BOM | `app/intake/reader.py` | `tests/test_intake.py::test_bom_preserves_identifier` |
| TRAP-03 | 按逻辑记录读取引号内换行 | `app/intake/reader.py` | `tests/test_intake.py::test_quoted_newline_is_one_logical_record` |
| TRAP-04 | 重复表头计入忽略行 | `app/intake/service.py` | `tests/test_intake.py::test_repeated_header_is_counted` |
| TRAP-05 | 异常列数隔离 | `app/intake/schema.py` | `tests/test_intake.py::test_extra_column_is_isolated` |
| TRAP-06 | 归一化全角数字和千分位 | `app/intake/numeric.py` | `tests/test_intake.py::test_full_width_and_grouping_are_canonical` |
| TRAP-07 | 空值行隔离 | `app/intake/numeric.py` | `tests/test_intake.py::test_sentinel_keeps_row_out_of_total` |
| TRAP-08 | 记录键保留前导零 | `app/intake/schema.py` | `tests/test_intake.py::test_bom_preserves_identifier` |
| TRAP-14 | 冲突版本都进入隔离 | `app/intake/resolve.py` | `tests/test_intake.py::test_duplicate_conflict_removes_both_copies` |
| TRAP-16 | 引号未闭合时拒收文件 | `app/intake/reader.py` | `tests/test_intake.py::test_unclosed_quote_invalidates_source` |
| TRAP-18 | 区分空文件、全空白和仅表头 | `app/intake/reader.py` | `tests/test_intake.py::test_empty_states_have_distinct_causes` |
| TRAP-26 | 拒绝旧版本复核 | `app/review/store.py` | `tests/test_review.py::test_review_optimistic_lock` |

## 领域反例

权重为课程互认学分。最多 12 门校内课程。外校课与校内课均只有一个名额，先最大化学分，再比较审批等级之和，最后按课程键稳定择一。最低学分由培养方案给出。

`sample_data/incoming.csv` 的 0003 组触发预检异常，0004 组触发业务判定。`tests/test_calculate.py` 还核对逐组结论和证据字段。

## 补录签发规则（同一申请跨批次）

| 约定 | 实现 | 验证 |
|---|---|---|
| 补录尽量多认学分 | `supplement/planner.py` 在剩余课程上复用最大权重 DP | `test_first_batch_maximizes_and_issues` |
| 已签发配对继续有效，不整份重算 | 签发即写 `claims`，补录只排除锁定课程，从不更新旧 claim | `test_issued_pairs_are_frozen`、`test_existing_run_approval_materializes_claims` |
| 同一外校课/校内课跨批次只抵扣一次 | `claims(application_id, external_course/local_course)` 双唯一索引 | `test_external_and_local_course_unique_across_batches` |
| 撞已签发课程，换组达到本批最低仍可签发 | 原方案 + 剩余方案双计算，剩余最优 ≥ 本批 authorization 学分即 `ISSUED`（`switched=true`） | `test_reroute_around_issued_courses_still_issues` |
| 两批并发撞同一门课只有一批签成 | 原子 `commit_issuance` + 唯一索引，输家结论 `RECALCULATE/COURSE_CLAIMED` | `test_parallel_batches_one_winner_one_recalculate`、`test_parallel_raising_variant_has_domain_issue` |
| 回执中断重试不重复计学分 | `(application_id, receipt_key)` 唯一回执，重放返回同一结论 | `test_receipt_retry_does_not_double_count`、`test_parallel_same_receipt_commits_once` |
| 工作台可查结论和原因 | `GET /api/applications/<id>` 与工作台“补录签发”面板 | `test_http_issue_and_query_application`、`test_supplement_conclusion_and_reason_are_queryable` |

申请编号缺省取 CSV 中唯一的 `case` 业务组；旧的 `PATCH /api/runs/<id> {status:'APPROVED'}` 也会把该 run 的获胜配对写入 claims。
