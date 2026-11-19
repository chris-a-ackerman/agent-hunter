# Agent-Hunter request/response log

Every request sent and every response received, in order (2026-11-18). Request bodies are in `tools/` and shown in full below. Responses are verbatim.

---

## 1. Agent-Hunter agent card

**Request:** `GET http://127.0.0.1:8940/.well-known/agent-card.json`

**Response (HTTP 200):**

```json
{"name":"agent-hunter","description":"Trial-then-Hire for invoice extraction: post a job, and Agent-Hunter tests candidate provider agents on it and recommends one, with confidence intervals.","url":"http://127.0.0.1:8940/","version":"0.1.0+7fa88f5","capabilities":{"streaming":false,"pushNotifications":false},"defaultInputModes":["text"],"defaultOutputModes":["text"],"skills":[{"id":"trial_then_hire","name":"Trial then hire","description":"Trial-then-Hire for invoice extraction: post a job, and Agent-Hunter tests candidate provider agents on it and recommends one, with confidence intervals.","tags":["trial_then_hire","invoice_extraction"]}],"mode":"offline"}
```

The server is in **offline** mode, so the candidates are mock providers.

---

## 2. Hire request (JSON-RPC `message/send`)

**Request:** `POST http://127.0.0.1:8940/`, `content-type: application/json` (file `tools/hire_envelope.json`)

```json
{"jsonrpc": "2.0", "id": 1, "method": "message/send", "params": {"message": {"kind": "message", "role": "user", "messageId": "msg-ap-0001", "parts": [{"kind": "text", "text": "{\"request_id\": \"ap-weekly-invoices-2026-11-18-01\", \"nonce\": \"a1b2c3\", \"job\": {\"requester_id\": \"ap-assistant\", \"description\": \"Weekly accounts-payable job: extract vendor, invoice number, date, currency, line items, subtotal, tax and total from supplier invoices.\", \"output_description\": \"One JSON object per invoice, matching output_schema.\", \"output_schema\": {\"type\": \"object\", \"properties\": {\"vendor_name\": {\"type\": \"string\"}, \"invoice_number\": {\"type\": [\"string\", \"null\"]}, \"invoice_date\": {\"type\": \"string\"}, \"currency\": {\"type\": \"string\"}, \"line_items\": {\"type\": \"array\"}, \"subtotal\": {\"type\": [\"number\", \"null\"]}, \"tax\": {\"type\": [\"number\", \"null\"]}, \"total\": {\"type\": \"number\"}}}, \"samples\": [{\"path\": \"samples/invoice_clean/sample_2.pdf\", \"answer\": {\"vendor_name\": \"Warner, Williamson and Dickerson\", \"invoice_number\": \"INV-42405\", \"invoice_date\": \"2026-05-04\", \"currency\": \"USD\", \"line_items\": [{\"description\": \"Courier delivery, same day\", \"quantity\": 24, \"unit_price\": 50.05, \"amount\": 1201.2}, {\"description\": \"Ballpoint pens, box of 50\", \"quantity\": 9, \"unit_price\": 17.52, \"amount\": 157.68}, {\"description\": \"Training workshop, per attendee\", \"quantity\": 25, \"unit_price\": 500.67, \"amount\": 12516.75}], \"subtotal\": 13875.63, \"tax\": 1110.05, \"total\": 14985.68}}], \"n_real_docs\": 50, \"job_budget_usd\": 1.0, \"trial_budget_usd\": 0.1, \"deadline_open_ack\": true}}"}]}}}
```

**Response (HTTP 200):**

```json
{"jsonrpc":"2.0","id":1,"result":{"kind":"task","id":"task-2918cfe58091","contextId":"ctx-e640b3e14e75","status":{"state":"completed"},"artifacts":[{"artifactId":"hire-result","name":"hire_result","parts":[{"kind":"text","text":"{\"candidates\":{\"considered\":[\"mock-a@1\",\"mock-b@1\",\"mock-c@1\",\"mock-d@1\",\"mock-e@1\"],\"excluded\":[]},\"hire\":{\"card_url\":\"http://localhost:8101/p/mock-a/.well-known/agent-card.json\",\"provider\":\"mock-a@1\",\"transport\":\"http\",\"url\":\"http://localhost:8101/p/mock-a/tasks\"},\"job_id\":\"job-f4e806dda2b2\",\"job_status\":\"ranked\",\"mode\":\"offline\",\"reason\":null,\"request_id\":\"ap-weekly-invoices-2026-11-18-01\",\"spend_usd\":{\"platform_usd\":0.0,\"providers_usd\":0.006500000000000001,\"total_usd\":0.006500000000000001},\"verdict\":{\"ci_95\":[1.0,1.0],\"cost_per_doc\":0.004,\"evidence\":\"trial-b4a662370e74\",\"ranking\":[{\"accuracy\":1.0,\"ci_95\":[1.0,1.0],\"cost_per_doc\":0.004,\"mean_latency_ms\":4.0,\"provider\":\"mock-a@1\",\"timeouts\":0},{\"accuracy\":0.8999999999999999,\"ci_95\":[0.8999999999999999,0.8999999999999999],\"cost_per_doc\":0.0005,\"mean_latency_ms\":7.0,\"provider\":\"mock-c@1\",\"timeouts\":0},{\"accuracy\":0.5,\"ci_95\":[0.5,0.5],\"cost_per_doc\":0.001,\"mean_latency_ms\":7.0,\"provider\":\"mock-b@1\",\"timeouts\":0},{\"accuracy\":0.0,\"ci_95\":[0.0,0.0],\"cost_per_doc\":0.0,\"mean_latency_ms\":null,\"provider\":\"mock-d@1\",\"timeouts\":1},{\"accuracy\":0.0,\"ci_95\":[0.0,0.0],\"cost_per_doc\":0.001,\"mean_latency_ms\":7.0,\"provider\":\"mock-e@1\",\"timeouts\":0}],\"rationale\":\"mock-a@1 leads and its confidence interval clears the runner-up's.\",\"recommended\":\"mock-a@1\",\"status\":\"recommended\",\"tie\":false,\"tied\":[],\"trial_accuracy\":1.0,\"valid_until\":null}}"}]}]}}
```

What it says: `mock-a@1` was recommended (trial accuracy 1.0, CI [1.0, 1.0] on a one-document trial, $0.004/doc), reachable over `http` at `http://localhost:8101/p/mock-a/tasks`. The trial cost $0.0065.

---

## 3. Recommended provider's card

**Request:** `GET http://localhost:8101/p/mock-a/.well-known/agent-card.json`

**Response (HTTP 200):**

```json
{"slug":"mock-a","name":"Mock A","description":"Test double with a known, documented accuracy (mocks/README.md).","version":"1","job_types":["invoice_extraction"],"price_per_doc_usd":0.01,"region":"global"}
```

---

## 4. Invoice extraction task to the hired provider

**Request:** `POST http://localhost:8101/p/mock-a/tasks`, `content-type: application/json` (file `tools/provider_task.json`)

```json
{"task_id": "ap-inv-0001", "requester_id": "ap-assistant", "document_url": "http://localhost:8200/d/1070b4b25d3c.pdf", "output_schema": {"type": "object", "properties": {"vendor_name": {"type": "string"}, "invoice_number": {"type": ["string", "null"]}, "invoice_date": {"type": "string"}, "currency": {"type": "string"}, "line_items": {"type": "array"}, "subtotal": {"type": ["number", "null"]}, "tax": {"type": ["number", "null"]}, "total": {"type": "number"}}}, "deadline_ms": 30000, "offered_price": 0.004}
```

**Response (HTTP 200):**

```json
{"task_id":"ap-inv-0001","output":{"vendor_name":"Anderson LLC","invoice_number":"BILL-9567","invoice_date":"2026-05-23","currency":"USD","line_items":[{"description":"27-inch monitor","quantity":9.0,"unit_price":301.74,"amount":2715.66},{"description":"Software licence, annual seat","quantity":21.0,"unit_price":411.8,"amount":8647.8}],"subtotal":11363.46,"tax":710.22,"total":12073.68},"cost_usd":0.004,"provider_version":"1"}
```

Result: vendor **Anderson LLC**, total **12073.68 USD**. The numbers add up: 2715.66 + 8647.80 = 11363.46 subtotal, + 710.22 tax = 12073.68.

**Caveat:** this is the offline demo invoice (`1070b4b25d3c`). The mock provider does not read the PDF; it returns a stored answer for that ID (AGENT_INTERFACE.md §4.3, §5.7). This shows the hire-and-hand-off flow working end to end. It is not a real extraction.
