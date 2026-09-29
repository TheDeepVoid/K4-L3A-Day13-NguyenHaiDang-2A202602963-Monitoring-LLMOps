# Evidence dùng để chấm bài

Danh sách chính thức, quy tắc chụp và cách nộp nằm tại [SUBMISSION.md](SUBMISSION.md). File này là checklist nhanh khi bạn thu thập evidence cá nhân.

## Evidence runtime bắt buộc

- [x] Kết quả cuối của `python -m pytest -q`.
- [x] `validate_logs.py` đạt tối thiểu 80/100.
- [x] `validate_dashboard.py` đạt 6/6.
- [x] Structured log có `correlation_id` và metadata.
- [x] PII giả đã được redact trong output thực tế.
- [x] Tên project Langfuse cá nhân và danh sách tối thiểu 10 traces do học viên tự tạo.
- [x] Một trace waterfall có root, retrieval và generation.
- [x] Trace metadata có correlation ID, prompt version/label, token và cost.
- [x] Prompt v1/v2 và bằng chứng promote/rollback.
- [x] Dashboard runtime đủ 6 panel, time range, đơn vị và threshold.
- [x] Incident metric, incident log và incident trace nối được bằng cùng correlation ID/khoảng sự cố.

## Artifact kiểm tra trực tiếp trên repo

Không cần chụp toàn bộ code. Dẫn link tới:

- `config/slo.yaml` và phần giải thích error budget trong `submission/REPORT.md`;
- `config/alert_rules.yaml` và `docs/alerts.md`;
- source, tests và commit history;
- `submission/REPORT.md`.

## Chất lượng evidence

- Evidence phải thuộc commit SHA được nộp và đúng challenge của lớp.
- Ảnh phải đọc được thông tin dùng để chấm, không phải ảnh trang trống.
- Che secret và PII; không dùng dữ liệu thật.
- Trace/prompt phải thuộc project cá nhân `day13-k4-l3a-<MSSV>`; không chụp trang API Keys.
- Đặt file trong `submission/evidence/`.
- Dẫn đường dẫn tương đối từ report, ví dụ `evidence/07-trace-waterfall.png`.
- Metric, log và trace của incident phải cùng chỉ về một nguyên nhân.
