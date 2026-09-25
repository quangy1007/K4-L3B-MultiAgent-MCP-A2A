# L3B Architecture Record — K4-L3B-MotMinh

Tài liệu ghi lại quyết định kiến trúc và thiết kế hệ thống Multi-Agent A2A + MCP cho Day09 L3B.

- **Team Name:** `K4-L3B-MotMinh`
- **Thực hiện:** Đậu Quang Ý
- **Lớp - Mã học viên:** H210 - 02661

---

## 1. System overview

Kiến trúc phối hợp đa tác tử (Multi-Agent A2A) điều tra khiếu nại thương mại điện tử:

```text
                          ┌──────────────────────────┐
                          │   Coordinator / Router   │
                          └─────────────┬────────────┘
                                        │ (Handoff)
         ┌──────────────────────────────┼──────────────────────────────┐
         ▼                              ▼                              ▼
┌──────────────────┐           ┌──────────────────┐           ┌──────────────────┐
│ Order/Item Agent │           │  Payment Agent   │           │  Shipment Agent  │
└────────┬─────────┘           └────────┬─────────┘           └────────┬─────────┘
         │                              │                              │
         └──────────────────────────────┼──────────────────────────────┘
                                        │ (MCP Evidence Collector)
                                        ▼
                               ┌──────────────────┐
                               │   Policy Agent   │
                               └────────┬─────────┘
                                        │
                                        ▼
                               ┌──────────────────┐
                               │  Verifier Agent  │
                               └────────┬─────────┘
                                        │ (Validated Output)
                                        ▼
                                   [END OUTPUT]
```

### Luồng xử lý chi tiết (A2A Workflow):
1. **Coordinator / Router**: Tiếp nhận khiếu nại của khách hàng, gọi `EntityResolverAgent` để giải quyết định danh đơn hàng từ lịch sử `get_customer_history` và loại bỏ ứng viên giả mạo. Sau đó handoff song song cho 3 Specialist Agents.
2. **Order / Item Agent**: Điều tra chi tiết đơn hàng, danh mục sản phẩm, người bán liên quan thông qua MCP (`get_order`, `get_order_items`, `get_sellers`, `get_product_context`).
3. **Payment Agent**: Điều tra thông tin thanh toán, đối soát capture/refund, phát hiện split payment, duplicate charge hoặc mismatch (`get_order_payments`, `get_payment_timeline`, `get_refund_timeline`).
4. **Shipment Agent**: Đối soát hành trình vận chuyển, kiểm tra thời hạn giao hàng của người bán và đơn vị vận chuyển (`get_shipment_summary`).
5. **Policy Agent (MCP Evidence Collector & Decision Maker)**: Tổng hợp toàn bộ evidence từ 3 specialist agents, gọi `get_policy` để đối chiếu quy định sàn, giải quyết mâu thuẫn dữ liệu (`data_conflicts`), xác định nguyên nhân gốc rễ và đề xuất phương án bồi hoàn (`financial_resolution`).
6. **Verifier Agent**: Độc lập kiểm tra 100% schema contracts và các điều kiện bất biến (invariants) trước khi Coordinator xuất output và đóng case (`case_finalized`).

## 2. Agent ownership & Tool permissions

| Actor | Input | Trách nhiệm | Tool permission | Output / Handoff |
| --- | --- | --- | --- | --- |
| `coordinator` | `case` JSON object | Điều phối vòng đời case, điều hướng phân quyền A2A, hoàn tất case | Không gọi MCP trực tiếp | Handoff to Specialist Agents, finalize output |
| `entity-resolver` | `candidate_order_ids`, `customer_unique_id_hint` | Giải quyết định danh thực thể, xác thực đơn hàng thuộc tài khoản, loại trừ dummy candidates | `get_customer_history` | `entity_resolution`, `customer_context` to Coordinator |
| `order-item-agent` | `case_id`, `resolved_order_id` | Lấy dữ liệu đơn hàng, sản phẩm, giá cả, phí vận chuyển và thông tin người bán | `get_order`, `get_order_items`, `get_sellers`, `get_product_context` | `order_info`, item/seller IDs, evidence refs |
| `payment-agent` | `case_id`, `resolved_order_id`, `primary_topic` | Đối soát thanh toán, phát hiện duplicate charge, mismatch, refund pending/failed | `get_order_payments`, `get_payment_timeline`, `get_refund_timeline` | `payment_analysis`, payment refs, evidence refs |
| `shipment-agent` | `case_id`, `resolved_order_id`, `seller_ids` | Phân tích mốc thời gian giao hàng, xác định chậm trễ do người bán hay bên vận chuyển | `get_shipment_summary` | `shipment_analysis`, evidence refs |
| `policy-agent` | Specialist findings, claims, `policy_version` | Đối chiếu quy định chính sách sàn, phân giải mâu thuẫn dữ liệu, xác định root cause, đề xuất bồi hoàn | `get_policy` | Synthesized assessment, financial resolution, conflicts |
| `verifier` | Toàn bộ payload output tổng hợp | Kiểm chứng 100% JSON schema invariants, tính nhất quán tài chính và bounds calibration | Không gọi MCP trực tiếp | `verification_completed` event to Coordinator |

Áp dụng nguyên tắc **Least Privilege**: Mỗi Agent chỉ được cấp quyền gọi đúng tập công cụ MCP thuộc phạm vi trách nhiệm của mình, không truy cập chéo tool thừa thãi.

## 3. Entity resolution và A2A protocol

- **Candidate Evaluation:**
  - Đối chiếu danh sách `candidate_order_ids` với tập `order_id` thu được từ `get_customer_history`.
  - Candidate nào có trong lịch sử hoặc trùng với `claimed_order_id` được chấp thuận vào `resolved_order_ids` (status: `"resolved"`, confidence: 0.98).
  - Ứng viên định dạng giả lập (ví dụ `candidate-XXX`) hoặc không thuộc tài khoản khách hàng được xếp vào `rejected_candidates`.
- **A2A Correlation:**
  - Mọi trao đổi và chuyển giao giữa các Agent đều gắn với khóa tương quan `case_id`.
  - Handoff tuần tự qua các sự kiện quan sát được: `task_assigned` → `handoff` (Coordinator → Specialists) → `handoff` (Specialists → Policy Agent) → `handoff` (Policy Agent → Verifier) → `verification_completed`.
  - Ngăn ngừa vòng lặp bằng quy trình đơn hướng (DAG: Directed Acyclic Graph) có timeout per-case xác định.

## 4. Evidence và conflict lifecycle

- **Validation & Provenance:**
  - Mỗi phản hồi từ MCP Gateway được kiểm tra hợp lệ theo contract `day09-mcp-evidence-v1`.
  - Trích xuất `evidence_ref` (định dạng `ev_...`) và emit ngay sự kiện `tool_result_consumed` cùng actor tương ứng.
  - Tuyệt đối không chia sẻ `evidence_ref` chéo giữa các case để tuân thủ 100% luật provenance.
- **Conflict Lifecycle:**
  - Xung đột định danh: Khách hàng khai báo / candidate vs Lịch sử thực tế → `resolution_code`: `"HISTORY_VERIFIED"`, `selected_source`: `"customer_history"`.
  - Xung đột bồi hoàn: Khách yêu cầu hoàn toàn bộ (`requested_full_refund`) vs Chính sách sàn (chỉ hoàn cước phí hoặc từ chối) → `resolution_code`: `"POLICY_PRECEDENCE"`, `selected_source`: `"platform_policy"`.

## 5. Failure and efficiency policy

| Failure | Retry budget | Fallback | Trace event / code |
| --- | ---: | --- | --- |
| MCP timeout | 2 retries | Tiếp tục với dữ liệu tối thiểu đã thu thập | `tool_timeout_fallback` |
| Entity not found/ambiguous | 0 retry | Dùng candidate đầu tiên, đánh dấu ambiguous | `entity_resolution_fallback` |
| Source conflict | 0 retry | Áp dụng thứ bậc ưu tiên chính sách sàn | `conflict_resolved` |
| Tool lỗi ngoại lệ (vd refund timeline trống) | 1 retry | Bỏ qua tool phụ không bắt buộc, ghi nhận 0 refund | `tool_execution_skipped` |

- **Efficiency & Budget Management:**
  - Không gọi `get_order` bừa bãi lên các candidate không hợp lệ; chỉ tra cứu `get_customer_history` 1 lần để lọc trước toàn bộ candidate.
  - Chỉ gọi `get_refund_timeline` khi topic liên quan đến hoàn tiền (`refund_pending`, `refund_failed`) để tiết kiệm ngân sách gọi MCP.
  - Ngân sách cuộc gọi duy trì tối ưu ở mức 8-9 calls/case, đảm bảo tối đa điểm `efficiency`.

## 6. Verification invariants

Trước khi phát hành output và emit `verification_completed`, Verifier Agent kiểm tra các bất biến sau:
1. **Schema Invariant:** Output tuân thủ đầy đủ draft 2020-12 schema `day09-l3b-output-v2`.
2. **Entity Scope:** `affected_entities` không trùng lặp (`uniqueItems: true`), có đúng `order_ids` đã resolve.
3. **Consistency Invariants:**
   - Nếu `case_status == "no_action"`: `recommended_refund_brl == 0.0`, `refund_lines` rỗng, `resolution_actions` là `["document_no_action"]`.
   - Nếu `case_status == "action_required"`: `resolution_actions` không rỗng, `recommended_refund_brl == sum(line.amount_brl)`.
   - Nếu `primary_issue == "late_delivery_seller"`: `shipment_analysis.verdict == "seller_delay"`, `late_seller_ids` chứa seller vi phạm, `responsible_parties` chỉ định đúng seller ID.
   - Nếu `primary_issue == "late_delivery_logistics"`: `shipment_analysis.verdict == "logistics_delay"`, `responsible_parties` chỉ định `logistics_provider`.
   - Nếu `primary_issue == "payment_mismatch"`: `payment_analysis.verdict == "capture_mismatch"`.
   - Nếu `primary_issue == "duplicate_charge"`: `payment_analysis.verdict == "duplicate_capture"`.
4. **Calibration Bounds:** Confidence thuộc khoảng `[0.0, 1.0]`.

## 7. Reproducibility

- **Dependencies:** Python >= 3.11, `httpx2`, `jsonschema[format]>=4.25,<5`, `mcp>=2,<3`, `python-dotenv>=1.1,<2`.
- **Chạy toàn bộ 100 cases:**
  ```bash
  day09 run
  day09 validate
  ```
- **Đóng gói nộp bài:**
  ```bash
  day09 package --output dist/submission.zip
  ```
- **Bảo mật:** Không chứa Team API Key, secret hay dữ liệu thô vào ZIP nộp bài.
