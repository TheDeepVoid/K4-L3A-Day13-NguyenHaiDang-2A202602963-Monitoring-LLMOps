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

- **Dashboard và sáu panel:**
- **SLO và lý do chọn:**
- **Cách tính error budget:**
- **Ba alert và runbook tương ứng:**

## 7. Điều tra challenge

- **Challenge ID:**
- **Khoảng thời gian điều tra:**
- **Triệu chứng từ metrics:**
- **Log line và correlation ID liên quan:**
- **Trace ID và span gây ảnh hưởng:**
- **Root cause:**
- **Fix action:**
- **Preventive measure:**

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
