# Báo cáo cá nhân — K4-L3A Day 13 Monitoring & LLMOps

> Mỗi học viên hoàn thiện một file duy nhất này. Khi dẫn evidence, dùng đường dẫn tương đối, ví dụ `evidence/07-trace-waterfall.png`.

## 1. Thông tin học viên

- **Họ và tên:** Nguyễn Hải Đăng
- **MSSV:** 2A202602963
- **Lớp:** L3A
- **Repository URL:** _(điền URL repo cá nhân khi push)_
- **Commit SHA cuối:** _(ghi lại sau CP4)_
- **Challenge ID:** `day13-k4-l3a-monitoring-llmops-v1`
- **Tên project Langfuse cá nhân:** `day13-k4-l3a-2A202602963`

## 2. Evidence index

Mỗi mục có **ảnh PNG** *và* **file text/HTML sinh ra thật**, để người chấm kiểm chứng lại bằng API key
của tôi mà không phải tin vào ảnh. Ảnh chụp bằng Playwright/Chromium từ đúng những file text/HTML đó.

| Evidence | Ảnh | File gốc (kiểm chứng được) |
|---|---|---|
| Baseline CP0 | — | `evidence/00-baseline-validate-logs.txt`, `00-baseline-validate-dashboard.txt`, `00-baseline-pytest.txt`, `00-baseline-metrics.json`, `00-baseline-traces.txt` |
| Pytest + kiểm tra cuối | `evidence/01-pytest.png` | `evidence/01-pytest-and-final-checks.txt` |
| Log validator | `evidence/02-log-validator.png` | `evidence/01-log-validator-final.txt` |
| Dashboard validator + đối chiếu jq | `evidence/03-dashboard-validator.png` | `evidence/11-dashboard-overview.txt` |
| Dashboard runtime | `evidence/11-dashboard-overview.png` | `evidence/11-dashboard-runtime.html` |
| PII redaction | `evidence/05-pii-redaction.png` | `evidence/02-pii-redaction.txt` |
| Trace list | `evidence/06-trace-list.png` | `evidence/06-trace-list.html` |
| **Trace waterfall** | `evidence/07-trace-waterfall.png` | `evidence/07-trace-waterfall.html` |
| Trace metadata | mục "Observation detail" trong ảnh waterfall | `evidence/07-trace-waterfall.html`, `evidence/06-trace-list-and-waterfall.txt` |
| Prompt versions | `evidence/09-prompt-versions.png` | `evidence/09-prompt-versions.txt` |
| Prompt promote/rollback | `evidence/10-prompt-rollback.png` | `evidence/10-prompt-rollback.txt` |
| SLO + 4 alert bắn thật | `evidence/12-incident-metric.png` | `evidence/12-alert-evaluation.txt` |
| Dashboard từng pha của challenge | `evidence/12-incident-dashboard-{healthy,incident,mitigated}.png` | `evidence/17-challenge-dashboard-phase-*.html` |
| Điều tra challenge CP3 | `evidence/13-incident-investigation.png` | `evidence/13-incident-investigation.txt` |
| Preventive measure 1 (queueing) | — | `evidence/14-queueing-before-after.txt` |
| Preventive measure 2 (prompt cache) | `evidence/15-prompt-cache-cold-start.png` | `evidence/15-prompt-cache-cold-start.txt` |
| Secret / PII scan | `evidence/16-secret-scan.png` | `evidence/16-secret-scan.txt` |

**Ảnh giao diện (UI) của Langfuse chưa có, và tôi nói rõ vì sao.** Web UI yêu cầu đăng nhập tương
tác; tôi chỉ có API key, không có mật khẩu, và tôi không hỏi bạn dán mật khẩu vào chat. Nên `06` và
`07` được dựng từ chính API `GET /api/public/v2/observations` bằng `scripts/render_trace_report.py`
rồi chụp lại. Mọi con số, thanh thời gian và metadata đều là giá trị Langfuse trả về — không có gì
vẽ tay — nhưng đây **không phải ảnh UI**. Nếu Lab Coach bắt buộc ảnh UI thì phải chụp lại khi đã đăng
nhập; script vẫn chạy lại được ngay.

## 3. Kết quả kỹ thuật

Baseline đo ở CP0 với `python scripts/load_test.py` (10 query mẫu), API chạy bằng starter code, chưa sửa TODO nào.
Evidence: `evidence/00-baseline-*.txt`, `evidence/00-baseline-metrics.json`.

Kết quả cuối đo ở CP4 trên một run sạch: restart API rồi `python scripts/load_test.py --concurrency 5`
(10 request). Evidence: `evidence/01-pytest-and-final-checks.txt`.

| Nội dung | Baseline | Kết quả cuối | Nhận xét |
|---|---|---|---|
| `validate_logs.py` | **30/100** | **100/100** | Mọi mục PASS sau CP1. PII được scrub ở tầng processor, không còn phụ thuộc `summarize_text` ở từng call site. |
| `validate_dashboard.py` | `HỢP LỆ: 6/6 panel` | `HỢP LỆ: 6/6 panel` | Contract đã đúng từ starter; giá trị nằm ở dashboard runtime thật, không phải ở validator. |
| `pytest` | 22 passed | **50 passed** | +28 test: correlation ID, PII pipeline thật, trace tree, prompt fallback, dashboard maths, alert rules, khoá ngưỡng theo incident thật. |
| Số traces hợp lệ | 10 trace, **chỉ root `AGENT`**, `model=null`, `usage=null` | **285 trace / 275 generation** có prompt link, 4 loại observation: `AGENT`, `RETRIEVER`, `SPAN`, `GENERATION` | Vượt yêu cầu ≥10 trace. |
| Số PII leak | 0 phát hiện | **0** ở cả log lẫn trace (quét toàn văn bản observation trả `none`) | Baseline "0" là do may mắn: `summarize_text` cắt 80 ký tự, chưa có processor. |
| Latency P95 / TTFT P95 | 837 ms / 50 ms | **151 ms / 50 ms** | P95 giảm vì sửa 2 nguyên nhân thật: bỏ hàng đợi event loop và warm prompt cache. TTFT 50 ms là `time.sleep(0.05)` của `FakeLLM`. |
| Retrieval success rate | 100% (10/10) | **100%** (10/10) | Và alert `retrieval_quality_drop` bắn đúng khi nó về 0% (`tool_fail`). |

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
good_event: 'event == "response_sent" and latency_ms <= 1000'
total_event: 'event == "request_received"'
```

Mẫu số là `request_received`, **không** phải `response_sent`, để request lỗi và request không trả lời
cũng tiêu budget thay vì biến mất khỏi phép tính.

**Ngưỡng này đã phải hiệu chỉnh lần thứ hai, và đó là bài học quan trọng nhất của phần này.** Bản
đầu dùng `latency_ms <= 3000` và biện minh bằng P95 đo được 877 ms. Nhưng 877 ms đó **bị nhiễm**: nó
chứa cả thời gian xếp hàng vì handler ghim event loop (mục 7). Sau khi sửa, baseline sạch là **151 ms**,
và 3000 ms trở thành **19.9 lần** headroom — lỏng tới mức challenge incident (2651 ms, tức 17.6 lần
baseline) **không còn vi phạm SLO nữa**. Nói cách khác: **sửa nút thắt đã âm thầm vô hiệu hóa chính
alert đang canh nó.** Một ngưỡng được hiệu chỉnh trên một baseline bị nhiễm sẽ tự ngắt phòng thủ ngay
khi nhiễm đó biến mất.

1000 ms là **6.6 lần** baseline sạch: xa hơn nhiễu thường ngày để không cảnh báo oan, nhưng đủ chặt
để bắt được cú nghẽn 2.5 s. Tôi ghi nguyên nhân hiệu chỉnh vào `config/slo.yaml` và
`config/alert_rules.yaml`, và thêm một test khoá ngưỡng vào đúng incident nó phải bắt, để thay đổi
baseline trong tương lai không thể lặp lại lỗi này trong im lặng.

Một điểm tôi **không** sửa: `config/dashboard.yaml` vẫn giữ `threshold.value: 3000` như đề bài
giao. Đó là contract bị chấm điểm và tài liệu nói rõ không tự chỉnh, nên tôi để nguyên và ghi rõ ở
đây rằng ngưỡng của contract (3000) khác ngưỡng SLO/alert của tôi (1000).

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
| `slow_answers` | P95 latency > **1000 ms** (hiệu chỉnh, xem trên) | high | 10 phút | `#day13-oncall` | backend-oncall | `docs/alerts.md#alert-1` | `fast_successful_requests` |
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
| baseline | — (0/4 firing) | P95 151 ms, error 0%, cost 0.022 USD/phút |
| `rag_slow` | `slow_answers` | P95 **2651 ms** > 1000 |
| `tool_fail` | `answers_failing` + `retrieval_quality_drop` | error **100%**, retrieval success **0%** |
| `cost_spike` | `token_cost_runaway` | **0.075 USD/phút** > 0.05 |

Bốn bản dashboard sau mỗi kịch bản được lưu cạnh đó (`12-dashboard-{baseline,rag_slow,tool_fail,
cost_spike}.html`) để so sánh trực quan.

## 7. Điều tra challenge

**Challenge ID:** `day13-k4-l3a-monitoring-llmops-v1` — cohort **K4**, seed **1311**, file
`config/challenge.json` do Lab Coach phát riêng. File này đã bị `.gitignore` và **không** được commit;
tôi kiểm tra lại bằng `git ls-files` trước khi nộp. Kịch bản: `rag_slow`, feature bị ảnh hưởng:
`monitoring`, `latency_threshold_ms: 2000`.

Lệnh đúng như `docs/CHECKPOINTS.md` CP3 yêu cầu — không truyền `--scenario`:

```bash
python scripts/inject_incident.py                          # đọc config/challenge.json
python scripts/load_test.py --challenge --concurrency 5
```

Evidence: `evidence/13-incident-investigation.png` + `evidence/13-incident-investigation.txt`, và
dashboard từng pha `evidence/12-incident-dashboard-{healthy,incident,mitigated}.png`.

**Khoảng thời gian điều tra.** File log cố ý chứa cả hai pha để nhìn thấy mốc chuyển trạng: `09:37:54`
là traffic bình thường của feature `monitoring`, ngay sau đó là 5 request của challenge.

**Triệu chứng từ metrics.**

| Pha | n | p50 (ms) | p95 (ms) | ttft_p95 | error | alert |
|---|---:|---:|---:|---:|---:|---|
| A — bình thường | 5 | 151 | 151 | 50 | 0 | 0/4 bắn |
| B — incident | 5 | 2651 | 2651 | 50 | 0 | **1/4 bắn** (`slow_answers`) |
| C — sau mitigation | 5 | 150 | 151 | 50 | 0 | 0/4 bắn |

`error` bằng 0 và `ttft_p95` không đổi: nếu chỉ nhìn error rate thì sự cố này vô hình, và `ttft` bất
biến là manh mối loại trừ ngay giả thuyết "model chậm".

**Một cái bẫy tôi gặp và ghi lại thay vì chỉnh số cho khớp.** Ở pha B, log chứa cả 5 request bình
thường lẫn 5 request của incident. Với chỉ 10 mẫu, p95 nearest-rank rơi vào 2651 ms, **không vượt**
ngưỡng 3000 ms cũ — nên nếu tôi chỉ nhìn SLO thì sẽ kết luận "chưa vi phạm". Tôi lọc bằng chính
`latency_threshold_ms: 2000` mà challenge đưa cho, vì đó là ngưỡng *dành riêng cho bài này*, tách
khỏi ngưỡng SLO.

**Log line và correlation ID liên quan.** 5/10 response vượt 2000 ms, đều thuộc feature
`monitoring`, mỗi session một ID:

```
req-e76a5eed  2651ms  ttft=50ms  session=k4-l3a-challenge-s05
req-dfe12917  2652ms  ttft=50ms  session=k4-l3a-challenge-s03
req-693c7ad0  2651ms  ttft=50ms  session=k4-l3a-challenge-s04
req-c390a17d  2652ms  ttft=50ms  session=k4-l3a-challenge-s01
req-a7c21966  2651ms  ttft=50ms  session=k4-l3a-challenge-s02
```

**Trace ID và span gây ảnh hưởng.** Mở trace bằng chính `correlation_id` đó (nó cũng là key trong trace
metadata, nên một định danh nối được log với trace):

| correlation_id | traceId | `retrieve-context` | `generate-response` |
|---|---|---|---|
| `req-c390a17d` | `0fc3ce216a6a83e01e8b169cf38bbd05` | **2.500 s (94.3%)** | 0.151 s (5.7%) |
| `req-dfe12917` | `3a926f70da4c43172ed02e818f3f627f` | **2.500 s (94.3%)** | 0.151 s (5.7%) |

Span chậm là `retrieve-context`; `generate-response` vẫn 0.151 s với `ttft = 0.05 s`,
`model = claude-sonnet-4-5`, cost bình thường. Ảnh waterfall:
`evidence/07-trace-waterfall.png`. Không có child observation thì trace chỉ có node
`lab-agent-run` và tôi **không thể** phân biệt "RAG chậm" với "model chậm" — đó là lý do phần CP2 đáng
giá, và cũng là lý do tôi thêm span `resolve-prompt` ở phần sau.

**Root cause.** `app/mock_rag.py::retrieve()` giữ request thêm 2.5 s khi `STATE["rag_slow"]` bật, và
toàn bộ thời gian đó nằm trong span `retrieve-context`. Root cause ở bước retrieval — không phải ở
model (`generate-response` bất biến, `ttft` bất biến) và không phải ở prompt: trace ghi
`prompt = day13-chat:1`, `prompt_source = langfuse`, **đúng version baseline**, nên loại trừ được giả
thuyết đổi prompt.

**Fix action.** Tắt incident rồi đo lại trên cùng workload:
`python scripts/inject_incident.py --scenario rag_slow --disable` → p50 về 150 ms, 0/4 alert bắn,
dashboard về đúng baseline (ảnh `12-incident-dashboard-mitigated.png`). Fix vĩnh lau dài hạn là đặt
deadline cho retrieval và phục vụ fallback thay vì chờ.

**Preventive measure 1 — hàng đợi làm sai lệch mọi tín hiệu.** Handler `/chat` là `async def` nhưng
gọi trực tiếp `agent.run()` blocking trên event loop, nên request xếp hàng nối tiếp nhau. Client quan
sát thấy request chậm tới **14.2 s** trong khi log chỉ ghi **3.5 s**: con số mà SLO, dashboard và alert
đánh giá thấp hơn thực tế **4.0 lần**. Sửa bằng `await run_in_threadpool(agent.run, ...)`:

| | client median | client max | server max | server thấp hơn thực tế |
|---|---:|---:|---:|---:|
| Trước | 13266 ms | 14172 ms | 3503 ms | **4.0×** |
| Sau | 2655 ms | 3494 ms | 3449 ms | **1.0×** |

Quan trọng hơn tốc độ là **tỉ lệ**: sau khi sửa, con số ghi ra log và con số người dùng chịu đựng
khớp nhau. Bằng chứng: `evidence/14-queueing-before-after.txt` (đo cả hai vế bằng `git stash`).

**Preventive measure 2 — sửa nút thắt làm tắt chính alert.** Sau khi sửa hàng đợi, ngưỡng 3000 ms
biến thành 19.9× baseline sạch và **không bắn** cho đúng incident này. Tôi hiệu chỉnh SLO và alert
xuống 1000 ms (6.6× baseline), ghi lý do vào file config, và thêm test khoá ngưỡng vào incident thật.
Đây là bài học lớn nhất của toàn bài: **ngưỡng được hiệu chỉnh trên một số đo bị nhiễm sẽ tự ngắt
phòng thủ khi số đo đó trở lại sự thật.**

**Preventive measure 3 — thời gian nằm ngoài mọi span.** `resolve_prompt` gọi Langfuse đồng bộ khi
cache rỗng; lần đầu sau restart mất ~900 ms so với 150 ms khi ấm, và thời gian đó **không nằm trong
span nào**. Tôi thêm span `resolve-prompt` và warm cache lúc khởi động: p50 sau restart về 151 ms
thay vì 898 ms. Bằng chứng: `evidence/15-prompt-cache-cold-start.png`.

## 8. Giải thích và tự đánh giá

**Một quyết định kỹ thuật quan trọng và lý do.** Chuyển việc scrub PII từ *từng call site* (gọi
`summarize_text` trước khi log) thành **một processor trong chain của structlog**. Lý do: bản gốc chỉ
che được PII ở những chỗ đã nhớ gọi hàm — một call site mới là rò rỉ ngay, và không có cách nào bắt
được điều đó bằng review. Sau khi chuyển, `scrub_value()` đi đệ quy mọi chuỗi trong event dict và được
đặt **trước** `JsonlFileProcessor`/`JSONRenderer`, nên không byte nào đã serialize chứa PII thô. Tôi
thêm một test chạy payload thô (không bọc hàm) qua pipeline thật để nguyên tắc này không bị xoá âm
thầm khi ai đó "dọn code".

**Một lỗi/blocker đã gặp.** Ba lỗi thật, tất cả đều do tin vào code thay vì kiểm chứng số đo:

1. `completion_start_time` lấy từ `time.perf_counter()` — đó là monotonic counter, không phải Unix
   timestamp — ra `timeToFirstToken = -1790668033`. Chỉ thấy được khi tôi tự tải trace về đọc.
2. Dashboard bỏ rơi chính dòng log mới nhất vì cửa sổ thời gian nửa mở (`< end`) trong khi neo vào
   `max(ts)`. Thấy được vì tôi in giá trị hiển thị cạnh bản tính lại bằng `jq`.
3. Alert `slow_answers` **không bắn** đúng lúc challenge xảy ra. Ở lần đầu, nguyên nhân là log
   trộn cả traffic bình thường nên p95 rơi vào 2651 ms, dưới ngưỡng 3000 ms. Tôi không chỉnh ngưỡng
   cho khớp mà ghi rõ cải bẫy trộn cửa sổ.
4. Nguyên nhân sâu hơn, phát hiện khi chạy challenge **chính thức**: ngưỡng 3000 ms vốn đã được hiệu
   chỉnh trên một baseline **bị nhiễm** (877 ms có chứa thời gian xếp hàng). Sau khi tôi sửa nút thắt
   hàng đợi, baseline sạch còn 151 ms và ngưỡng cũ biến thành 19.9× headroom — **sửa nút thắt đã âm
   thầm vô hiệu hóa chính alert đang canh nó.** Đây là lỗi của chính tôi trong bài của tôi, và là
   thứ tôi ghi vào `config/slo.yaml` cùng một test khoá ngưỡng.

**Cách tìm nguyên nhân và xử lý.** Đi đúng thứ tự: metrics cho triệu chứng và mốc thời gian → log cho
`correlation_id` → trace cho span. Ở CP3, metrics cho biết p50 nhảy 150 → 2651 ms **trong khi error
bằng 0 và `ttft_p95` không đổi**; `ttft` bất biến loại trừ ngay giả thuyết "model chậm"; trace cho thấy
`retrieve-context` chiếm 94.3% thời gian. Không có child observation thì bước cuối này không làm
được — trace chỉ có một node.

**Cách hiểu luồng Metrics → Logs → Traces.** Ba tầng trả lời ba câu hỏi khác nhau và **không thay thế
nhau**: metrics trả lời "có vấn đề không, lúc nào, kiểu gì" (rẻ, nhưng trộn cửa sổ thì sai); logs trả
lời "request nào" (cần correlation ID); traces trả lời "bước nào" (cần span con đúng kiểu). Bài học
tôi rút ra và đã kiểm chứng bằng số: ở CP3, *cả ba tầng đều đúng mà vẫn thiếu* — client chờ 14.2 s
trong khi mọi tầng đều báo 3.5 s, vì hàng đợi nằm ngoài mọi span. Sửa bằng `run_in_threadpool` đưa tỉ
lệ "server nói thấp hơn thực tế" từ 4.0× xuống 1.0×.

**Vai trò của prompt version, token/cost, SLO hoặc rollback trong vận hành LLM.** Prompt version cho
phép trả lời "câu trả lời tuần trước có khác gì không" — ở đây nó còn loại trừ được giả thuyết "do
đổi prompt" khi điều tra `rag_slow`, vì trace cho thấy đúng version baseline. Token/cost là đơn vị
tiền thật: tôi **ingest** `cost_details` thay vì để Langfuse tự suy luận, nên con số trong trace bằng
đúng con số trong log và trên panel cost — nếu lệch nhau thì mọi báo cáo đều đáng nghi. SLO biến "người
dùng chờ lâu" thành con số có ngân sách, và `run_in_threadpool` chính là hành động mà error budget buộc
phải làm. Rollback prompt là biện pháp rẻ nhất khi chất lượng đi xuống: đổi label là đổi hành vi, không
cần deploy.

**Điều quan trọng nhất đã học.** *Số đo không nói dối, nhưng nó có thể đo sai thứ — và ngưỡng của
bạn có thể chết âm thầm.* Bốn ví dụ từ chính bài này: log bị scrub sai tầng nên "0 PII leak" ở
baseline; cửa sổ trộn làm p95 không vi phạm; đo latency trong tiến trình nên thấp hơn thực tế 4×;
và tệ nhất, **ngưỡng alert được hiệu chỉnh trên một baseline bị nhiễm nên tự ngắng phòng thủ ngay khi
nhiễm được dọn sạch**. Trong cả bốn, số đều "đúng theo cách tính" và sai về quyết định.

Vì vậy tôi rút ra bốn nguyên tắc và áp dụng ngay: (1) scrub ở tầng sink chứ không ở call site;
(2) mọi con số trong báo cáo đều có một cách tính lại độc lập bằng `jq`; (3) **alert phải được bắn
thật ít nhất một lần trên một sự cố thật**, nếu không thì đó chỉ là văn bản; (4) **hiệu chỉnh lại ngưỡng
mỗi khi baseline đổi**, và coi việc alert ngừng bắn là tín hiệu phải kiểm tra lại ngưỡng chứ không
phải tin tưởng mừng.

**Sự cố bảo mật do chính công cụ của tôi gây ra — và cách tôi xử lý.** Khi chạy
`secret_scan.sh`, script in ra giá trị key dạng rõ để chứng minh "không tìm thấy". Vì file kết quả
đó là evidence được commit, **key đã lọt vào 2 commit cục bộ** — và chính script đó báo rằng bản thân
nó là một leak. Đây là loại lỗi nguy hiểm nhất trong phần bảo mật: công cụ kiểm tra rò rỉ trở thành
bản thân sự rò rỉ.

Cách xử lý, theo đúng thứ tự:

1. **Sửa công cụ trước**: script giờ mask giá trị (`pk***49`) và tự loại trừ output của chính nó khỏi
   phép grep trong working tree.
2. **Kiểm tra mức độ lan truyền**: `git log origin/main..HEAD` = 11 commit, tức **chưa commit nào
   được push**; `origin/main` vẫn ở commit starter `13b6066`. Key chưa từng rời khỏi máy này.
3. **Dọn sạch history** thay vì để lại key đã "rotate" mà vẫn nằm trong object store: `git
   filter-branch` chỉ tới `34615de^..HEAD`, xoá `refs/original`, `reflog expire`, `git gc --prune=now`.
4. **Xác minh lại bằng chính script**: quét toàn bộ object store của repo — không còn blob nào chứa
   key. Bằng chứng: `evidence/16-secret-scan.png`, mục "not in git history" cả hai key.

**Khuyến nghị vẫn nên rotate key trên phía Langfuse.** Lý do: dù key chưa từng bị push, nó đã nằm
trong git object cục bộ trong một khoảng thời gian, và nguyên tắc xử lý sự cố credential là coi nó là
đã lộ. Tôi không tự rotate được vì cần đăng nhập UI. Đây là việc còn lại của tôi.

**Hạn chế hoặc phần chưa hoàn thành.**

- **Ảnh UI của Langfuse vẫn thiếu.** Tôi đã chụp được ảnh cho mọi mục bằng Playwright/Chromium,
  gồm waterfall và trace list — nhưng chúng được **dựng từ API rồi chụp**, không phải ảnh chụp web
  UI, vì UI cần đăng nhập tương tác và tôi không có mật khẩu (tôi không hỏi bạn dán mật khẩu vào
  chat). Mọi giá trị đều do Langfuse trả về, nhưng nếu rubric bắt buộc ảnh UI thì đây là khoảng
  trống thật và cần chụp lại khi đã đăng nhập.
- `FakeLLM` không đọc prompt, nên v1 và v2 không thể so sánh chất lượng (rubric cũng không chấm điểm đó).
- `app/metrics.py` vẫn là biến toàn cục trong bộ nhớ: sống lâu hơn một process thì sẽ mất, và
  percentile trên danh sách tăng dần sẽ tốn bộ nhớ. Đủ cho lab, chưa đủ cho production — đó là lý do
  dashboard của tôi đọc từ file log chứ không đọc từ `/metrics`.
- Chưa làm CI, audit log, hay cost optimization (các mục bonus), vì ưu tiên hoàn tất và kiểm chứng
  các yêu cầu bắt buộc trước.

## 9. Checklist trước khi nộp

- [x] Kết quả và evidence thuộc commit SHA cuối — mỗi checkpoint là một commit riêng
      (`git log --oneline`), evidence commit kèm.
- [x] Tất cả ảnh/output mở được bằng đường dẫn tương đối — mục 2 liệt kê từng file; các bản
      dashboard HTML mở trực tiếp, không cần mạng.
- [x] Incident evidence nối đúng metric → log → trace — `evidence/13-incident-investigation.txt`:
      cùng `correlation_id` xuất hiện ở cả ba tầng, dẫn tới span `retrieve-context` 2.500 s.
- [x] Trace/prompt evidence thuộc project Langfuse cá nhân (`day13-k4-l3a-2A202602963`) và không
      lộ key/secret — evidence chỉ chứa trace ID, không có key.
- [x] Repository chạy lại được theo README — `python -m pytest -q` 50 passed, `validate_logs.py`
      100/100, `validate_dashboard.py` 6/6, `evaluate_alerts.py` im lặng trên traffic bình thường.
- [x] Không có secret, API key, PII thô hoặc evidence của người khác/lớp khác —
      `evidence/16-secret-scan.txt` quét working tree, git history **và toàn bộ object store** bằng
      chính giá trị key trong `.env`; kết quả `clean`. File evidence PII đã che giá trị để không trông
      giống rò rỉ.
- [x] **Sự cố rò rỉ do công cụ kiểm tra gây ra đã được xử lý đầy đủ** (mục 8): script đã sửa để mask,
      key chưa từng được push, history đã được dọn sạch và xác minh lại bằng script.
- [ ] **Việc còn lại của tôi:** điền URL repo cá nhân, commit SHA cuối, và nộp lên LMS/Codelabs.
- [x] **Challenge chính thức đã chạy** — `config/challenge.json` (cohort K4) đã được dùng đúng
      như tài liệu: `inject_incident.py` không truyền `--scenario`, `load_test.py --challenge`.
      Mục 7 có metric → log → trace → root cause, fix và 3 preventive measure.
- [ ] **Còn thiếu:** ảnh chụp trực tiếp giao diện Langfuse (xem mục 2 và 8) — cần phiên đăng nhập.
- [ ] **Việc của tôi:** (a) **rotate key Langfuse** trong Project Settings → API Keys, (b) điền URL
      repo cá nhân và commit SHA cuối, (c) nộp lên LMS/Codelabs.
