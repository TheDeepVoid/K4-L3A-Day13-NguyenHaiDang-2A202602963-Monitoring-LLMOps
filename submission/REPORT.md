# Báo cáo cá nhân — K4-L3A Day 13 Monitoring & LLMOps

> Mỗi học viên hoàn thiện một file duy nhất này. Khi dẫn evidence, dùng đường dẫn tương đối, ví dụ `evidence/07-trace-waterfall.png`.

## 1. Thông tin học viên

- **Họ và tên:** Nguyễn Hải Đăng
- **MSSV:** 2A202602963
- **Lớp:** L3A
- **Repository URL:** _(điền URL repo cá nhân khi push)_
- **Commit SHA cuối:** _(ghi lại sau CP4)_
- **Challenge ID:** _(điền sau CP3)_
- **Tên project Langfuse cá nhân:** `day13-k4-l3a-2A202602963`

## 2. Evidence index

Điền đúng đường dẫn tới evidence thực tế. Có thể đổi tên hoặc dùng nhiều ảnh nếu cần.

Evidence dạng text/JSON (baseline và output lệnh) được commit cạnh ảnh; ảnh PNG dùng cho
trace waterfall, prompt version, rollback và dashboard runtime.

| Evidence | Đường dẫn |
|---|---|
| Pytest cuối | `evidence/01-pytest.png` (kèm `evidence/01-pytest.txt`) |
| Log validator | `evidence/02-log-validator.png` |
| Dashboard validator | `evidence/03-dashboard-validator.png` |
| Structured log | `evidence/04-structured-log.png` |
| PII redaction | `evidence/05-pii-redaction.png` |
| Trace list | `evidence/06-trace-list.png` |
| Trace waterfall | `evidence/07-trace-waterfall.png` |
| Trace metadata | `evidence/08-trace-metadata.png` |
| Prompt versions | `evidence/09-prompt-versions.png` |
| Prompt rollback | `evidence/10-prompt-rollback.png` |
| Dashboard runtime | `evidence/11-dashboard-overview.png` |
| Incident metric | `evidence/12-incident-metric.png` |
| Incident log | `evidence/13-incident-log.png` |
| Incident trace | `evidence/14-incident-trace.png` |

## 3. Kết quả kỹ thuật

Baseline đo ở CP0 với `python scripts/load_test.py` (10 query mẫu), API chạy bằng starter code, chưa sửa TODO nào.
Evidence: `evidence/00-baseline-*.txt`, `evidence/00-baseline-metrics.json`.

| Nội dung | Baseline | Kết quả cuối | Nhận xét |
|---|---|---|---|
| `validate_logs.py` | **30/100** | **100/100** | Mọi mục PASS sau CP1. PII giờ được scrub ở tầng processor, không còn phụ thuộc `summarize_text` ở từng call site. |
| `validate_dashboard.py` | `HỢP LỆ: 6/6 panel` | _xem CP2_ | Contract YAML đã đúng ngay từ starter; chỉ là chưa có dashboard runtime thật. |
| `pytest` | 22 passed | _xem CP4_ | Test public bảo vệ contract, không cover TODO. |
| Số traces hợp lệ | 10 trace, **chỉ root `AGENT`**, `model=null`, `usage=null` | _xem CP2_ | 10 observation `lab-agent-run`, không có child retriever/generation; `version=local-v1` vì chưa tạo prompt trong Langfuse. |
| Số PII leak | 0 phát hiện | _xem CP1_ | Sample query chứa email + SĐT + thẻ; chỉ được che vì `summarize_text` cắt ngắn 80 ký tự. |
| Latency P95 / TTFT P95 | **837 ms / 50 ms** | _xem CP2_ | P95 cao vì request đầu tiên lạnh; TTFT 50 ms là `time.sleep(0.05)` trong `FakeLLM`. |
| Retrieval success rate | 100% (10/10 `tool_success=true`) | _xem CP3_ | Chưa bật incident nào. |

## 4. Logging và PII

**Cách tạo/nhận và truyền correlation ID.** `CorrelationIdMiddleware` (`app/middleware.py`) chạy
trên mọi request:

1. `clear_contextvars()` đầu mỗi request — structlog contextvars sống trong context-local dict,
   không xóa thì request sau kế thừa context của request trước và rò rỉ vào file log.
2. Đọc header `x-request-id`; chỉ dùng lại nếu khớp allow-list `^[A-Za-z0-9_.:-]{1,64}$`, ngược lại
   sinh mới `req-<8 hex>`. Header là input không tin cậy mà đi thẳng ra file log và response header,
   nên chặn CRLF/quote/quá độ dài ngay ở biên.
3. `bind_contextvars(correlation_id=...)`, lưu vào `request.state.correlation_id` để handler dùng lại.
4. Trả về `x-request-id` và `x-response-time-ms` trên response.

`correlation_id` được truyền xuống `LabAgent.run(...)` và ghi vào trace metadata, nên một ID nối được
cả log lẫn trace.

**Metadata được ghi vào structured log.** `main.chat()` bind trước dòng `request_received`, nên mọi
log line sau đó của request đó dùng chung một context: `user_id_hash` (sha256 12 ký tự, **không**
ghi `user_id` thô), `session_id`, `feature`, `model`, `env`, `correlation_id`. Trường này khớp với
`app/schemas.py::LogRecord` và `config/logging_schema.json`, và là đúng những field mà sáu panel của
dashboard đọc.

**Cách bảo đảm PII được scrub trước khi ghi.** `scrub_event` là một structlog processor trong
`app/logging_config.py`, đặt sau `format_exc_info` (để traceback đã render cũng được scrub) và
**trước** `JsonlFileProcessor` + `JSONRenderer` (để không byte nào đã serialize chứa PII thô). Nó
gọi `scrub_value()` đi đệ quy trên **mọi** chuỗi trong event dict, không chỉ trường `payload`:
một call site mới quên bọc `summarize_text` vẫn không rò PII. Key của dict được giữ nguyên để
tên field vẫn query được. Traceback đã render và nhánh lỗi (`request_failed`, `payload.detail`)
đi qua cùng một processor.

**Cách kiểm chứng kết quả.** `evidence/02-pii-redaction.txt`: một request chứa đủ 6 lớp PII
(email, SĐT, CCCD, thẻ, hộ chiếu, địa chỉ) → dòng log chỉ còn `[REDACTED_*]`; `grep` PII thô trên
toàn bộ `data/logs.jsonl` trả `0`; nhánh lỗi 500 cũng đã scrub. `evidence/01-log-validator-final.txt`:
validator 100/100 với detector regex **độc lập** của chính validator. 11 test PII mới trong
`tests/test_pii.py`, gồm một test chạy qua pipeline structlog thật.

## 5. Tracing và prompt versioning

**Cách xác nhận traces do chính tôi tạo trong project cá nhân.** Toàn bộ evidence lấy từ project
`day13-k4-l3a-2A202602963` bằng API key trong `.env` của tôi, đọc qua `GET /api/public/v2/observations`
(v1 `/api/public/traces` bị từ chối với org tạo sau 16/09/2026:
`LEGACY_API_UNAVAILABLE_FOR_NEW_ORGANIZATION`). Mỗi trace mang `userId` đã hash và `sessionId` riêng;
không trace nào lấy từ project dùng chung.

**Cấu trúc root/retrieval/generation.** Mỗi request = 1 trace, cây 3 node là anh em dưới cùng root:

```
day13-agent-request  (trace)  userId=<sha256[:12]>, sessionId, env=dev, tags=[lab, feature, model]
└── lab-agent-run            type=AGENT      input/output = câu hỏi + câu trả lời (đã scrub)
    ├── retrieve-context     type=RETRIEVER  input=query, output=documents, doc_count
    └── generate-response    type=GENERATION model, max_tokens, usage, cost, prompt link, TTFT
```

`retrieve-context` dùng type `retriever` (không phải `span` chung chung) để phân tích RAG và Agent
Graph hoạt động, và để tách được "RAG chậm" khỏi "model chậm" — đúng thứ CP3 cần.
`generate-response` mang `model`, `model_parameters`, `usage_details`, `cost_details` và liên kết tới
đúng prompt version; `completion_start_time` được set nên `timeToFirstToken` có giá trị thật (0.05s,
khớp `time.sleep(0.05)` trong `FakeLLM`). Cost được **ingest** (`cost_details`) nên con số trong trace
bằng đúng con số trong log và trên panel cost, thay vì để Langfuse tự suy luận giá.

Một lỗi thật đã bắt được khi làm: lần đầu `completion_start_time` lấy từ `time.perf_counter()` — đó là
monotonic counter, không phải Unix timestamp — khiến `timeToFirstToken = -1790668033`. Đã sửa sang
`datetime.now(timezone.utc)`.

Root observation giữ `capture_input=False, capture_output=False`: nếu bật, decorator sẽ gửi **toàn bộ
tham số hàm** (gồm `user_id` và `message` thô) lên Langfuse. Trace input/output được set tường minh
với text đã scrub, và set **sau cùng** để trace hiện đúng câu hỏi + câu trả lời.

**Cách nối trace với log.** `correlation_id` sinh ở middleware được truyền vào `LabAgent.run(...)` và
ghi vào **trace metadata của root observation**; cùng ID đó xuất hiện trong `data/logs.jsonl`. Evidence
`06-trace-list-and-waterfall.txt` in cả hai danh sách để đối chiếu từng dòng.

**Prompt name:** `day13-chat` (type `text`, trong project cá nhân). Giữ nguyên ba biến
`{{feature}}`, `{{docs}}`, `{{message}}` để khớp `prompt_management.py`.

**Version/label baseline:** v1 — labels `baseline` + `production`. Nội dung: system prompt, `<context>`
chứa docs, `<question>` chứa message, và câu lệnh "chỉ trả lời dựa trên context, không có thì nói thẳng
là không biết".

**Version/label candidate:** v2 — labels `candidate`. Khác v1 **đúng một câu**: thêm
`Keep the answer under 80 words.` (thay đổi nhỏ nhất có thể test được — mỗi lần chỉ đổi một nguyên nhân).

**Trace ID của mỗi version:**

| Label | Version | correlation_id | traceId | promptId |
|---|---|---|---|---|
| `baseline` | 1 | `req-194fe8ea` | `8fb0a16f8f4876d5bcbf72229fcb2e1e` | `fa797682-3cea-401f-9c55-b62a3ccfbd32` |
| `candidate` | 2 | `req-2a4246fa` | `4bd7543129b270201c939f88c41d57d6` | `e3dd3dff-49dd-4993-8495-5318ad8b7c8b` |
| `production` (sau promote) | 2 | `req-c502ba79` | `4e3574d6d360920cccbaa152f8d71140` | `e3dd3dff-...` |
| `production` (sau rollback) | 1 | `req-33fe38ba` | `195a35be78eb67cbe987be8ade232d39` | `fa797682-...` |

**Cách promote và rollback `production`.** Không làm thủ công bằng click mà bằng script chạy lại được —
`scripts/prompt_ops.py` (`create-v1`, `create-v2`, `promote 2`, `rollback`, `show`), dùng
`create_prompt(labels=...)` và `update_prompt(new_labels=...)`. Chuỗi lệnh thật đã chạy:

```bash
python scripts/prompt_ops.py create-v1     # v1, labels baseline+production
python scripts/prompt_ops.py create-v2     # v2, label candidate
# LANGFUSE_PROMPT_LABEL=baseline  -> restart API -> 1 request -> trace 8fb0a16f... (v1)
# LANGFUSE_PROMPT_LABEL=candidate -> restart API -> 1 request -> trace 4bd75431... (v2)
python scripts/prompt_ops.py promote 2     # production -> v2
# 1 request -> trace 4e3574d6... phục vụ v2 (prompt có dòng "Keep the answer under 80 words.")
python scripts/prompt_ops.py rollback      # production -> v1
# 1 request -> trace 195a35be... phục vụ lại v1 (không còn dòng đó)
```

Điểm quan trọng: khi Langfuse không trả được prompt, `prompt_management.py` ghi
`prompt_source=local-fallback` và `version=local-v1` thay vì bịa version. Evidence CP0 cho thấy
baseline đúng là `local-v1`; sau khi tạo prompt, cùng code đó trả `source=langfuse` với version thật.

**Lưu ý trung thực về v1 vs v2.** `FakeLLM` trong `app/mock_llm.py` trả về một câu trả lời cố định và
**không đọc prompt**, nên v1 và v2 không thể khác nhau về chất lượng đầu ra. Rubric cũng ghi rõ điểm
được chấm là *khả năng truy xuất version, đổi label và rollback có bằng chứng*, không phải prompt nào
"hay hơn". Tôi ghi rõ điều này thay vì dựng ra một so sánh chất lượng giả.

## 6. Dashboard, SLO và alerts

**Dashboard và sáu panel.** Không cài thêm công cụ: `scripts/build_dashboard.py` đọc
`config/dashboard.yaml` và `data/logs.jsonl` rồi sinh một file HTML tự chứa
(`submission/evidence/11-dashboard-runtime.html`, có SVG inline và meta refresh 30s). Panel id, title,
unit, events, threshold đều **đọc từ contract** chứ không hard-code; script sẽ báo lỗi nếu config
thêm panel mà nó chưa biết. Percentile dùng chung `app.metrics.percentile` nên dashboard, `/metrics`
và test đều dùng cùng một quy tắc nearest-rank.

| Panel | Nguồn | Tổng hợp | Threshold | Baseline của tôi |
|---|---|---|---|---|
| latency | `response_sent` | p50/p95/p99 + ttft_p95 | p95 ≤ 3000 ms | 150 / 877 / 877 / 50 ms |
| traffic | `request_received` | count, req/phút | ≥ 1 req/phút | 10 req |
| errors | `request_received`, `request_failed` | error_rate_pct, breakdown, tool_success_rate_pct | error_rate ≤ 2% | 0.0% / 100% |
| cost | `response_sent` | tổng theo phút, tổng | tổng ≤ 2.5 USD | 0.0242 USD |
| tokens | `response_sent` | tổng theo field | ≤ 50000 | 900 in / 1435 out |
| quality | `response_sent` | mean | mean ≥ 0.75 | 0.88 |

Validator: `HỢP LỆ: 6/6 panel`. Evidence `11-dashboard-overview.txt` in từng giá trị hiển thị cạnh
bản **tính lại độc lập bằng jq** từ cùng file log — hai cách khớp nhau ở cả 6 panel.

**SLO và lý do chọn.** SLO chính `fast_successful_requests`, mục tiêu 99.5% trong 28 ngày:

```yaml
good_event: 'event == "response_sent" and latency_ms <= 3000'
total_event: 'event == "request_received"'
```

Mẫu số là `request_received`, **không** phải `response_sent`, để request lỗi và request không trả lời
cũng tiêu budget thay vì biến mất khỏi phép tính. Ngưỡng 3000 ms chọn từ baseline thật của tôi: P95
đo được là 877 ms, nên 3000 ms còn dư khoảng 3.4 lần; quan trọng hơn, ngưỡng này **bị incident
`rag_slow` phá vỡ** (P95 nhảy lên 3361–5416 ms tùy độ trễ), tức là SLO bắt được sự cố thật thay vì
nằm ngoài tầm với. Tôi giữ nguyên ngưỡng của starter vì nó có lý do, và viết lý do đó ra thay vì
đổi số cho vừa.

Hai SLO phụ: `retrieval_available` (99%) vì retrieval hỏng thì có 500 nhưng retrieval *sai âm thầm*
thì không, và `daily_token_cost` (95% ngày dưới 2.5 USD) vì chi phí tăng đều sẽ không bao giờ làm
P95 vượt ngưỡng.

**Cách tính error budget.** `error_budget_percent = 100 − target_percent` → 0.5% trong 28 ngày.
Ở lưu lượng baseline 10 request/phút (= 403.200 request/28 ngày) thì budget cho phép **2016 request
xấu**. Đọc theo hướng khác: đang ổn định thì có thể hỏng ~100 request/phút trong khoảng 20 phút trước
khi hết budget, còn trải đều cả tháng thì phải xấu 10.080 request mới hết. Chính sách ghi trong
`config/slo.yaml`: dùng hơn 50% budget thì đóng băng thay đổi prompt/model, hết budget thì dừng mọi
việc không khẩn cấp.

**Bốn alert và runbook tương ứng** (lab yêu cầu ba; tôi thêm một cái vì có một triệu chứng thật mà ba
rule kia không bắt được):

| Alert | Triệu chứng | Severity | Duration | Slack | Owner | Runbook | SLO |
|---|---|---|---|---|---|---|---|
| `slow_answers` | P95 latency > 3000 ms | high | 10 phút | `#day13-oncall` | backend-oncall | `docs/alerts.md#alert-1` | `fast_successful_requests` |
| `answers_failing` | error rate > 2% | critical | 5 phút | `#day13-oncall` | backend-oncall | `docs/alerts.md#alert-2` | `fast_successful_requests` |
| `retrieval_quality_drop` | retrieval success < 90% | medium | 15 phút | `#day13-llm-platform` | ml-platform | `docs/alerts.md#alert-3` | `retrieval_available` |
| `token_cost_runaway` | > 0.05 USD/phút | medium | 15 phút | `#day13-llm-platform` | ml-platform | `docs/alerts.md#alert-4` | `daily_token_cost` |

Tất cả đều viết trên **triệu chứng người dùng**, không phải trên nguyên nhân: "vector store chậm" là
nguyên nhân — nó đưa kết luận cho người trực và im lặng khi một nguyên nhân khác gây ra cùng đau đớn.
Alert thứ ba tồn tại vì retrieval trả về rỗng vẫn cho HTTP 200 với câu trả lời tự tin, mà latency
và error rate đều không nhúc nhích.

Alert không chỉ được *mô tả* mà được **chạy thật**: `scripts/evaluate_alerts.py` tính lại từng rule
từ `data/logs.jsonl` (cùng nguồn với dashboard, nên hai bên không thể mâu thuẫn). Evidence
`12-alert-evaluation.txt` cho thấy cả bốn rule im lặng trên baseline và **bắn đúng sự cố của mình**:

| Workload | Alert bắn | Giá trị |
|---|---|---|
| baseline | — (0/4 firing) | P95 877 ms, error 0%, cost 0.024 USD/phút |
| `rag_slow` | `slow_answers` | P95 **3361 ms** > 3000 |
| `tool_fail` | `answers_failing` + `retrieval_quality_drop` | error **100%**, retrieval success **0%** |
| `cost_spike` | `token_cost_runaway` | **0.0874 USD/phút** > 0.05 |

Bốn bản dashboard sau mỗi kịch bản được lưu cạnh đó (`12-dashboard-{baseline,rag_slow,tool_fail,
cost_spike}.html`) để so sánh trực quan.

## 7. Điều tra challenge

**Challenge ID:** **không có — đây là practice, không phải challenge chính thức.**
`config/challenge.json` do Lab Coach phát riêng cho từng lớp và đã bị `.gitignore`; repo này không có
file đó và `scripts/inject_incident.py` sẽ báo lỗi nếu thiếu. Theo `docs/CHECKPOINTS.md` CP3 và
`README.md`, khi chưa có file được phát thì tiếp tục dùng `--scenario` để luyện tập và **không** được
tự tạo hay lấy challenge của lớp khác. Tôi ghi rõ điều này thay vì ghi một Challenge ID giả.
Kịch bản dùng để điều tra: `rag_slow` (practice).

**Khoảng thời gian điều tra:** file log cố ý chứa cả hai pha để nhìn thấy mốc chuyển trạng — phút
`08:51` là traffic bình thường, phút `08:52` là lúc bật incident.

**Triệu chứng từ metrics.** Bảng theo phút của `scripts/investigate.py`:

| Phút | n | p50 (ms) | p95 (ms) | ttft_p95 | cost |
|---|---:|---:|---:|---:|---:|
| `08:51` bình thường | 7 | 150 | 889 | 50 | 0.0159 |
| `08:52` sự cố | 13 | 2651 | 2651 | 50 | 0.0266 |

p50 nhảy từ 150 ms lên 2651 ms, **error vẫn bằng 0**, `ttft_p95` không đổi. Nếu chỉ nhìn error rate thì
sự cố này vô hình; và `ttft` bất biến là manh mối loại trừ ngay giả thuyết "model chậm".

Một cái bẫy tôi gặp và ghi lại: vì log chứa cả hai pha, p95 nearest-rank của 20 mẫu (10 bình thường,
10 sự cố) rơi vào **2651 ms**, tức **không vượt** ngưỡng 3000 ms của SLO. Trộn cửa sổ làm che mất sự
vi phạm. Nên tôi lọc bằng ngưỡng 2000 ms — đúng nghĩa "chậm hơn baseline 10 lần" — thay vì bắt đầu từ
con số SLO.

**Log line và correlation ID liên quan.** 10/20 response vượt 2000 ms, ví dụ:

```
req-dbbb5aea  latency=2651ms  ttft=50ms  tokens=89/109
req-52f1083b  latency=2651ms  ttft=50ms  tokens=41/133
```

**Trace ID và span gây ảnh hưởng.** Mở trace bằng chính `correlation_id` đó (nó cũng là key trong
trace metadata, nên một định danh nối được log với trace):

| correlation_id | traceId | `retrieve-context` | `generate-response` |
|---|---|---|---|
| `req-dbbb5aea` | `057392136688d5f0ddab949dfb183cc9` | **2.500 s (94.3%)** | 0.151 s (5.7%) |
| `req-52f1083b` | `b0e62dc7f8cf90f6044bae5ebc30a73b` | **2.500 s (94.3%)** | 0.150 s (5.7%) |

Span chậm là `retrieve-context`; `generate-response` vẫn 0.15 s với `ttft=0.05s`, model và cost bình
thường. Không có child observation thì trace chỉ có node `lab-agent-run` và tôi **không thể** phân
biệt "RAG chậm" với "model chậm" — đây chính là lý do phần CP2 đáng giá.

**Root cause.** `app/mock_rag.py::retrieve()` còn 2.5 s khi `STATE["rag_slow"]` bật, và toàn bộ thời
gian đó nằm trong span `retrieve-context`. Root cause ở bước retrieval, không phải ở model và không
phải ở prompt: trace ghi `prompt=day13-chat:1`, `prompt_source=langfuse`, đúng version với baseline, nên
loại trừ được giả thuyết prompt.

**Fix action.** Tắt incident rồi đo lại trên cùng workload:
`python scripts/inject_incident.py --scenario rag_slow --disable` → p50 về 150 ms, 0/4 alert bắn,
dashboard về đúng baseline. Fix vĩnh lau dài hạn là đặt deadline cho retrieval và phục vụ fallback
thay vì chờ.

**Preventive measure — phát hiện thêm trong lúc điều tra.** Client quan sát thấy request chậm tới
**14.2 s** trong khi log chỉ ghi **3.5 s**: tức con số mà SLO, dashboard và alert đánh giá thấp hơn thực
tế **4.0 lần**. Nguyên nhân: handler `/chat` là `async def` nhưng gọi trực tiếp `agent.run()` blocking
(vốn dùng `time.sleep`) trên event loop, nên các request xếp hàng nối tiếp nhau. Sửa bằng
`await run_in_threadpool(agent.run, ...)`:

| | client median | client max | server max | server thấp hơn thực tế |
|---|---:|---:|---:|---:|
| Trước | 13266 ms | 14172 ms | 3503 ms | **4.0×** |
| Sau | 2655 ms | 3494 ms | 3449 ms | **1.0×** |

Quan trọng hơn tốc độ là **tỉ lệ**: sau khi sửa, con số ghi ra log và con số người dùng chịu đựng khớp
nhau, nghĩa là SLO cuối cùng đo đúng thứ người dùng cảm nhận. Bằng chứng:
`evidence/14-queueing-before-after.txt` (đo cả hai vế trên cùng workload bằng `git stash`).

Ba hệ quả tôi ghi lại trong `docs/alerts.md`: (1) đo latency ở biên chứ không chỉ trong tiến trình;
(2) khi nghi ngờ nghẽn hàng đợi, so số client với số server; (3) `ttft_p95` bất biến là manh mối loại
trừ "model chậm".
## 8. Giải thích và tự đánh giá

- **Một quyết định kỹ thuật quan trọng và lý do:**
- **Một lỗi/blocker đã gặp:**
- **Cách tìm nguyên nhân và xử lý:**
- **Cách hiểu luồng Metrics → Logs → Traces:**
- **Vai trò của prompt version, token/cost, SLO hoặc rollback trong vận hành LLM:**
- **Điều quan trọng nhất đã học:**
- **Hạn chế hoặc phần chưa hoàn thành, nếu có:**

## 9. Checklist trước khi nộp

- [ ] Kết quả và evidence thuộc commit SHA cuối.
- [ ] Tất cả ảnh/output mở được bằng đường dẫn tương đối.
- [ ] Incident evidence nối đúng metric → log → trace.
- [ ] Trace/prompt evidence thuộc project Langfuse cá nhân và ảnh không lộ key/secret.
- [ ] Repository chạy lại được theo README.
- [ ] Không có secret, API key, PII thô hoặc evidence của người khác/lớp khác.
- [ ] URL repo và commit SHA cuối đã được nộp trên LMS/Codelabs.
