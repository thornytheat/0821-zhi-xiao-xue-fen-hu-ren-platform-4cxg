# 阶段接口

`POST /api/stages/<stage>` 接收 `multipart/form-data`。字段 `sample=dirty`、`sample=clean` 或 `file=<CSV>` 三选一。阶段为 `intake`、`prepare`、`calculate`、`policy`、`balance`。预览不入库。

`POST /api/runs` 接收同样的输入，执行全批并返回运行报告。`GET /api/runs` 返回最近 50 批。`GET /api/runs/<id>` 查询一批和当前复核状态。

`PATCH /api/runs/<id>` 接收 `revision`、`status` 和 `note`。状态使用 `APPROVED` 或 `HELD`。冲突返回 409，客户端读取新版本后重试。**当状态为 `APPROVED` 时，该 run 各业务组的获胜配对会物化为课程抵扣（claims），业务组号即申请编号。**

`GET /api/runs/<id>/evidence` 下载内含运行清单、行号隔离、业务证据和复核信息的 JSON。

# 补录签发（同一申请跨批次）

补录批次围绕“申请编号（`application_id`，缺省取 CSV 中唯一的 `case` 业务组）”组织。已签发配对写入 `claims` 并冻结；同一申请下同一 `external_course` 或同一 `local_course` 都有唯一索引，跨批次只能抵扣一次。

- `POST /api/applications/<application_id>/supplements`：提交补录 CSV 并核算/签发。支持 `multipart/form-data`（`file`、`sample=supplement`、`receipt=<回执键>`）、JSON（`records`/`csv`/`receipt`）或直接 CSV 请求体。
- `POST /api/supplements`：同上，申请编号放在 `application_id` 字段。
- `POST /api/applications/<application_id>/issue-pairs`：JSON 提交人工“换组配对”后的 `pairs` 与 `receipt`。
- `GET /api/applications/<application_id>`：申请汇总，含 `items`（每批的 `status/conclusion`、`reason/reason_message`、学分、配对、回执）与 `claims`（已锁定抵扣）。
- `GET /api/applications/<application_id>/supplements`、`GET /api/supplements?application_id=`、`GET /api/supplements/<id>`：补录批次结论与原因。
- `GET /api/claims?application_id=`、`GET /api/applications`：抵扣记录与申请列表。

结论码：`ISSUED`（已签发）、`ISSUABLE`（试算可签发）、`CREDIT_SHORTFALL`（无撞课但达不到本批最低学分）、`RECALCULATE`（原方案撞已签发/并发占用且剩余配对不足，需重新核算；`reason=COURSE_CLAIMED`）。原方案撞课但换组后达到本批最低时直接返回 `ISSUED`，并带 `switched=true` 与新 `pairs`。

并发两批撞同一门课时，数据库唯一索引保证只有一批写入该 claim；另一批得到 `RECALCULATE`。同一 `receipt`（或无回执键时同一申请+相同内容）重试返回同一 `id` 与同一份结论，不重复写 claims、不重复计学分。

服务层入口为 `app.supplement.service.issue_supplement(store, application_id, data, receipt=None)`（参数顺序与关键字有多种兼容形式），查询用 `application_summary`、`get_supplement`、`list_supplements`、`evaluate_supplement`/`recalculate`。

