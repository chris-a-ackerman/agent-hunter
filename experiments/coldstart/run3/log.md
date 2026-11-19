# Agent-Hunter request/response log

Every HTTP request sent and every response received, in order, on 2026-11-18 (server time 2026-11-19 UTC).
Bodies are copied exactly as sent and received. Request bodies are also saved in `tools/`.

---

## 1. Fetch the Agent-Hunter agent card

### Request

```
GET http://127.0.0.1:8940/.well-known/agent-card.json
```

### Response — `HTTP/1.1 200 OK` (date: Thu, 19 Nov 2026 02:38:13 GMT)

```json
{"name":"agent-hunter","description":"Trial-then-Hire for invoice extraction: post a job, and Agent-Hunter tests candidate provider agents on it and recommends one, with confidence intervals.","url":"http://127.0.0.1:8940/","version":"0.1.0+0c4ade5","capabilities":{"streaming":false,"pushNotifications":false},"defaultInputModes":["text"],"defaultOutputModes":["text"],"skills":[{"id":"trial_then_hire","name":"Trial then hire","description":"Trial-then-Hire for invoice extraction: post a job, and Agent-Hunter tests candidate provider agents on it and recommends one, with confidence intervals.","tags":["trial_then_hire","invoice_extraction"]}],"mode":"offline"}
```

Note: `mode` is `offline`, so the candidates are mock providers (AGENT_INTERFACE.md §5.7).

---

## 2. Post the hire request (JSON-RPC `message/send`)

### Request

```
POST http://127.0.0.1:8940/
content-type: application/json
```

Body (file `tools/hire.json`):

```json
{"jsonrpc": "2.0", "id": 1, "method": "message/send", "params": {"message": {"kind": "message", "role": "user", "messageId": "msg-ap-0001", "parts": [{"kind": "text", "text": "{\"request_id\": \"ap-weekly-invoices-0001\", \"job\": {\"requester_id\": \"ap-assistant\", \"description\": \"Weekly accounts-payable job: extract vendor, invoice number, date, currency, line items, subtotal, tax and total from supplier invoices.\", \"output_description\": \"One JSON object per invoice, matching output_schema.\", \"output_schema\": {\"type\": \"object\", \"properties\": {\"vendor_name\": {\"type\": \"string\"}, \"invoice_number\": {\"type\": [\"string\", \"null\"]}, \"invoice_date\": {\"type\": \"string\"}, \"currency\": {\"type\": \"string\"}, \"line_items\": {\"type\": \"array\"}, \"subtotal\": {\"type\": [\"number\", \"null\"]}, \"tax\": {\"type\": [\"number\", \"null\"]}, \"total\": {\"type\": \"number\"}}, \"required\": [\"vendor_name\", \"invoice_number\", \"invoice_date\", \"currency\", \"line_items\", \"subtotal\", \"tax\", \"total\"]}, \"samples\": [{\"path\": \"samples/invoice_clean/sample_2.pdf\", \"answer\": {\"vendor_name\": \"Warner, Williamson and Dickerson\", \"invoice_number\": \"INV-42405\", \"invoice_date\": \"2026-05-04\", \"currency\": \"USD\", \"line_items\": [{\"description\": \"Courier delivery, same day\", \"quantity\": 24, \"unit_price\": 50.05, \"amount\": 1201.2}, {\"description\": \"Ballpoint pens, box of 50\", \"quantity\": 9, \"unit_price\": 17.52, \"amount\": 157.68}, {\"description\": \"Training workshop, per attendee\", \"quantity\": 25, \"unit_price\": 500.67, \"amount\": 12516.75}], \"subtotal\": 13875.63, \"tax\": 1110.05, \"total\": 14985.68}}], \"n_real_docs\": 50, \"job_budget_usd\": 1.0, \"trial_budget_usd\": 0.1, \"deadline_open_ack\": true}}"}]}}}
```

### Response — `HTTP/1.1 200 OK` (date: Thu, 19 Nov 2026 02:38:22 GMT)

```json
{"jsonrpc":"2.0","id":1,"result":{"kind":"task","id":"task-95581835e17c","contextId":"ctx-346bdb67bc7f","status":{"state":"completed"},"artifacts":[{"artifactId":"hire-result","name":"hire_result","parts":[{"kind":"text","text":"{\"candidates\":{\"considered\":[\"mock-a@1\",\"mock-b@1\",\"mock-c@1\",\"mock-d@1\",\"mock-e@1\"],\"excluded\":[]},\"hire\":{\"card_url\":\"http://localhost:8101/p/mock-a/.well-known/agent-card.json\",\"provider\":\"mock-a@1\",\"transport\":\"http\",\"url\":\"http://localhost:8101/p/mock-a/tasks\"},\"job_id\":\"job-74d5a8061437\",\"job_status\":\"ranked\",\"mode\":\"offline\",\"reason\":null,\"request_id\":\"ap-weekly-invoices-0001\",\"spend_usd\":{\"platform_usd\":0.0,\"providers_usd\":0.006500000000000001,\"total_usd\":0.006500000000000001},\"verdict\":{\"ci_95\":[1.0,1.0],\"cost_per_doc\":0.004,\"evidence\":\"trial-9a7ccf6d2611\",\"ranking\":[{\"accuracy\":1.0,\"ci_95\":[1.0,1.0],\"cost_per_doc\":0.004,\"mean_latency_ms\":8.0,\"provider\":\"mock-a@1\",\"timeouts\":0},{\"accuracy\":0.7999999999999999,\"ci_95\":[0.7999999999999999,0.7999999999999999],\"cost_per_doc\":0.0005,\"mean_latency_ms\":6.0,\"provider\":\"mock-c@1\",\"timeouts\":0},{\"accuracy\":0.55,\"ci_95\":[0.55,0.55],\"cost_per_doc\":0.001,\"mean_latency_ms\":5.0,\"provider\":\"mock-b@1\",\"timeouts\":0},{\"accuracy\":0.0,\"ci_95\":[0.0,0.0],\"cost_per_doc\":0.0,\"mean_latency_ms\":null,\"provider\":\"mock-d@1\",\"timeouts\":1},{\"accuracy\":0.0,\"ci_95\":[0.0,0.0],\"cost_per_doc\":0.001,\"mean_latency_ms\":5.0,\"provider\":\"mock-e@1\",\"timeouts\":0}],\"rationale\":\"mock-a@1 leads and its confidence interval clears the runner-up's.\",\"recommended\":\"mock-a@1\",\"status\":\"recommended\",\"tie\":false,\"tied\":[],\"trial_accuracy\":1.0,\"valid_until\":null}}"}]}]}}
```

Summary: `job_status: ranked`, verdict `recommended` → **mock-a@1** (trial accuracy 1.0, CI [1.0, 1.0] on a single trial document, $0.004/doc). The hire URL is `http://localhost:8101/p/mock-a/tasks`, transport `http`. Trial spend was $0.0065.

---

## 3. Fetch the hired provider's card

### Request

```
GET http://localhost:8101/p/mock-a/.well-known/agent-card.json
```

### Response — `HTTP/1.1 200 OK` (date: Thu, 19 Nov 2026 02:38:30 GMT)

```json
{"slug":"mock-a","name":"Mock A","description":"Test double with a known, documented accuracy (mocks/README.md).","version":"1","job_types":["invoice_extraction"],"price_per_doc_usd":0.01,"region":"global"}
```

List price is $0.01/doc. That is under our per-doc budget of $1.00 / 50 = $0.02.

---

## 4. Send one invoice to the hired provider (ProviderTask)

### Request

```
POST http://localhost:8101/p/mock-a/tasks
content-type: application/json
```

Body (file `tools/task1.json`):

```json
{"task_id": "ap-task-0001", "requester_id": "ap-assistant", "document_url": "http://localhost:8200/d/000145785a2b.pdf", "output_schema": {"type": "object", "properties": {"vendor_name": {"type": "string"}, "invoice_number": {"type": ["string", "null"]}, "invoice_date": {"type": "string"}, "currency": {"type": "string"}, "line_items": {"type": "array"}, "subtotal": {"type": ["number", "null"]}, "tax": {"type": ["number", "null"]}, "total": {"type": "number"}}, "required": ["vendor_name", "invoice_number", "invoice_date", "currency", "line_items", "subtotal", "tax", "total"]}, "deadline_ms": 30000, "offered_price": 0.01}
```

### Response — `HTTP/1.1 200 OK` (date: Thu, 19 Nov 2026 02:38:35 GMT)

```json
{"task_id":"ap-task-0001","output":{"vendor_name":"Niemeier","invoice_number":"IN-88515","invoice_date":"2026-09-25","currency":"EUR","line_items":[{"description":"Cleaning supplies kit","quantity":23.0,"unit_price":40.02,"amount":920.46},{"description":"27-inch monitor","quantity":21.0,"unit_price":187.83,"amount":3944.43},{"description":"USB-C docking station","quantity":1.0,"unit_price":239.58,"amount":239.58},{"description":"Ballpoint pens, box of 50","quantity":1.0,"unit_price":9.8,"amount":9.8},{"description":"Shipping and handling","quantity":11.0,"unit_price":36.19,"amount":398.09}],"subtotal":5512.36,"tax":1047.35,"total":6559.71},"cost_usd":0.004,"provider_version":"1"}
```

Summary: invoice extracted. **Vendor: Niemeier. Total: 6559.71 EUR** (invoice IN-88515, 2026-09-25). Provider charged $0.004.
My own check: the output has all 8 schema fields with the right types. The line amounts sum to the subtotal (5512.36), subtotal + tax = total (6559.71), and the tax is 19% of the subtotal.

---

## Caveat

The server is in **offline** mode. mock-a does not read the PDF. It answers from a stored answer key for this doc ID, so this result shows that the hire and hand-off work, not that real extraction works. The doc server also has no upload method (AGENT_INTERFACE.md §4.3), so I could not send one of our own invoices.
