# Agent-Hunter session log

Every HTTP request sent and every response received, verbatim, in order.

## 2026-11-18T21:18:00 — Fetch Agent-Hunter's agent card

**Request:** `GET http://127.0.0.1:8940/.well-known/agent-card.json`

**Response:** status 200

```
{"name":"agent-hunter","description":"Trial-then-Hire for invoice extraction: post a job, and Agent-Hunter tests candidate provider agents on it and recommends one, with confidence intervals.","url":"http://127.0.0.1:8940/","version":"0.1.0+90a05bd","capabilities":{"streaming":false,"pushNotifications":false},"defaultInputModes":["text"],"defaultOutputModes":["text"],"skills":[{"id":"trial_then_hire","name":"Trial then hire","description":"Trial-then-Hire for invoice extraction: post a job, and Agent-Hunter tests candidate provider agents on it and recommends one, with confidence intervals.","tags":["trial_then_hire","invoice_extraction"]}],"mode":"offline"}
```

## 2026-11-18T21:18:10 — Hire: message/send with job posting

**Request:** `POST http://127.0.0.1:8940/`

```json
{"jsonrpc": "2.0", "id": 1, "method": "message/send", "params": {"message": {"kind": "message", "role": "user", "messageId": "msg-ap-0001", "parts": [{"kind": "text", "text": "{\"request_id\": \"ap-weekly-invoices-20261118-01\", \"job\": {\"requester_id\": \"ap-assistant\", \"description\": \"Weekly: turn supplier invoices into structured JSON (vendor, invoice number, date, currency, line items, subtotal, tax, total).\", \"output_description\": \"One JSON object per invoice, matching output_schema.\", \"output_schema\": {\"type\": \"object\", \"properties\": {\"vendor_name\": {\"type\": \"string\"}, \"invoice_number\": {\"type\": [\"string\", \"null\"]}, \"invoice_date\": {\"type\": \"string\"}, \"currency\": {\"type\": \"string\"}, \"line_items\": {\"type\": \"array\"}, \"subtotal\": {\"type\": [\"number\", \"null\"]}, \"tax\": {\"type\": [\"number\", \"null\"]}, \"total\": {\"type\": \"number\"}}}, \"samples\": [{\"path\": \"samples/invoice_clean/sample_2.pdf\", \"answer\": {\"vendor_name\": \"Warner, Williamson and Dickerson\", \"invoice_number\": \"INV-42405\", \"invoice_date\": \"2026-05-04\", \"currency\": \"USD\", \"line_items\": [{\"description\": \"Courier delivery, same day\", \"quantity\": 24, \"unit_price\": 50.05, \"amount\": 1201.2}, {\"description\": \"Ballpoint pens, box of 50\", \"quantity\": 9, \"unit_price\": 17.52, \"amount\": 157.68}, {\"description\": \"Training workshop, per attendee\", \"quantity\": 25, \"unit_price\": 500.67, \"amount\": 12516.75}], \"subtotal\": 13875.63, \"tax\": 1110.05, \"total\": 14985.68}}], \"n_real_docs\": 50, \"job_budget_usd\": 1.0, \"trial_budget_usd\": 0.1, \"deadline_open_ack\": true}}"}]}}}
```

**Response:** status 200

```
{"jsonrpc":"2.0","id":1,"result":{"kind":"task","id":"task-6dd98815b01c","contextId":"ctx-3de91d79abe8","status":{"state":"completed"},"artifacts":[{"artifactId":"hire-result","name":"hire_result","parts":[{"kind":"text","text":"{\"candidates\":{\"considered\":[\"mock-a@1\",\"mock-b@1\",\"mock-c@1\",\"mock-d@1\",\"mock-e@1\"],\"excluded\":[]},\"job_id\":\"job-416b7e6d011a\",\"job_status\":\"ranked\",\"mode\":\"offline\",\"reason\":null,\"request_id\":\"ap-weekly-invoices-20261118-01\",\"spend_usd\":{\"platform_usd\":0.0,\"providers_usd\":0.006500000000000001,\"total_usd\":0.006500000000000001},\"verdict\":{\"ci_95\":[0.7999999999999999,0.7999999999999999],\"cost_per_doc\":0.001,\"evidence\":\"trial-cb4c5f6c7aa0\",\"ranking\":[{\"accuracy\":0.7999999999999999,\"ci_95\":[0.7999999999999999,0.7999999999999999],\"cost_per_doc\":0.001,\"mean_latency_ms\":3.0,\"provider\":\"mock-b@1\",\"timeouts\":0},{\"accuracy\":0.7,\"ci_95\":[0.7,0.7],\"cost_per_doc\":0.004,\"mean_latency_ms\":4.0,\"provider\":\"mock-a@1\",\"timeouts\":0},{\"accuracy\":0.2,\"ci_95\":[0.2,0.2],\"cost_per_doc\":0.0005,\"mean_latency_ms\":4.0,\"provider\":\"mock-c@1\",\"timeouts\":0},{\"accuracy\":0.0,\"ci_95\":[0.0,0.0],\"cost_per_doc\":0.0,\"mean_latency_ms\":null,\"provider\":\"mock-d@1\",\"timeouts\":1},{\"accuracy\":0.0,\"ci_95\":[0.0,0.0],\"cost_per_doc\":0.001,\"mean_latency_ms\":4.0,\"provider\":\"mock-e@1\",\"timeouts\":0}],\"rationale\":\"mock-b@1 leads and its confidence interval clears the runner-up's.\",\"recommended\":\"mock-b@1\",\"status\":\"recommended\",\"tie\":false,\"tied\":[],\"trial_accuracy\":0.7999999999999999,\"valid_until\":null}}"}]}]}}
```

## 2026-11-18T21:18:23 — tasks/get: re-read the hire task, looking for any provider endpoint

**Request:** `POST http://127.0.0.1:8940/`

```json
{"jsonrpc":"2.0","id":2,"method":"tasks/get","params":{"id":"task-6dd98815b01c"}}
```

**Response:** status 200

```
{"jsonrpc":"2.0","id":2,"result":{"kind":"task","id":"task-6dd98815b01c","contextId":"ctx-3de91d79abe8","status":{"state":"completed"},"artifacts":[{"artifactId":"hire-result","name":"hire_result","parts":[{"kind":"text","text":"{\"candidates\":{\"considered\":[\"mock-a@1\",\"mock-b@1\",\"mock-c@1\",\"mock-d@1\",\"mock-e@1\"],\"excluded\":[]},\"job_id\":\"job-416b7e6d011a\",\"job_status\":\"ranked\",\"mode\":\"offline\",\"reason\":null,\"request_id\":\"ap-weekly-invoices-20261118-01\",\"spend_usd\":{\"platform_usd\":0.0,\"providers_usd\":0.006500000000000001,\"total_usd\":0.006500000000000001},\"verdict\":{\"ci_95\":[0.7999999999999999,0.7999999999999999],\"cost_per_doc\":0.001,\"evidence\":\"trial-cb4c5f6c7aa0\",\"ranking\":[{\"accuracy\":0.7999999999999999,\"ci_95\":[0.7999999999999999,0.7999999999999999],\"cost_per_doc\":0.001,\"mean_latency_ms\":3.0,\"provider\":\"mock-b@1\",\"timeouts\":0},{\"accuracy\":0.7,\"ci_95\":[0.7,0.7],\"cost_per_doc\":0.004,\"mean_latency_ms\":4.0,\"provider\":\"mock-a@1\",\"timeouts\":0},{\"accuracy\":0.2,\"ci_95\":[0.2,0.2],\"cost_per_doc\":0.0005,\"mean_latency_ms\":4.0,\"provider\":\"mock-c@1\",\"timeouts\":0},{\"accuracy\":0.0,\"ci_95\":[0.0,0.0],\"cost_per_doc\":0.0,\"mean_latency_ms\":null,\"provider\":\"mock-d@1\",\"timeouts\":1},{\"accuracy\":0.0,\"ci_95\":[0.0,0.0],\"cost_per_doc\":0.001,\"mean_latency_ms\":4.0,\"provider\":\"mock-e@1\",\"timeouts\":0}],\"rationale\":\"mock-b@1 leads and its confidence interval clears the runner-up's.\",\"recommended\":\"mock-b@1\",\"status\":\"recommended\",\"tie\":false,\"tied\":[],\"trial_accuracy\":0.7999999999999999,\"valid_until\":null}}"}]}]}}
```

## 2026-11-18T21:18:23 — Probe documented mock-provider base URL (/.well-known/agent-card.json) to reach hired provider mock-b@1

**Request:** `GET http://localhost:8101/.well-known/agent-card.json`

**Response:** status 404

```
{"detail":"Not Found"}
```

## 2026-11-18T21:18:23 — Probe documented mock-provider base URL (/mock-b/.well-known/agent-card.json) to reach hired provider mock-b@1

**Request:** `GET http://localhost:8101/mock-b/.well-known/agent-card.json`

**Response:** status 404

```
{"detail":"Not Found"}
```

## 2026-11-18T21:18:23 — Probe documented mock-provider base URL (/) to reach hired provider mock-b@1

**Request:** `GET http://localhost:8101/`

**Response:** status 404

```
{"detail":"Not Found"}
```

## 2026-11-18T21:18:31 — Probe mock-provider server's OpenAPI listing for a route to mock-b@1

**Request:** `GET http://localhost:8101/openapi.json`

**Response:** status 200

```
{"openapi":"3.1.0","info":{"title":"Agent-Hunter mock providers","version":"0.1.0"},"paths":{"/p/{slug}/.well-known/agent-card.json":{"get":{"summary":"Get Card","operationId":"get_card_p__slug___well_known_agent_card_json_get","parameters":[{"name":"slug","in":"path","required":true,"schema":{"type":"string","title":"Slug"}}],"responses":{"200":{"description":"Successful Response","content":{"application/json":{"schema":{}}}},"422":{"description":"Validation Error","content":{"application/json":{"schema":{"$ref":"#/components/schemas/HTTPValidationError"}}}}}}},"/p/{slug}/tasks":{"post":{"summary":"Post Task","operationId":"post_task_p__slug__tasks_post","parameters":[{"name":"slug","in":"path","required":true,"schema":{"type":"string","title":"Slug"}}],"requestBody":{"required":true,"content":{"application/json":{"schema":{"$ref":"#/components/schemas/ProviderTask"}}}},"responses":{"200":{"description":"Successful Response","content":{"application/json":{"schema":{}}}},"422":{"description":"Validation Error","content":{"application/json":{"schema":{"$ref":"#/components/schemas/HTTPValidationError"}}}}}}},"/p/{slug}/a2a/.well-known/agent-card.json":{"get":{"summary":"Get A2A Card","operationId":"get_a2a_card_p__slug__a2a__well_known_agent_card_json_get","parameters":[{"name":"slug","in":"path","required":true,"schema":{"type":"string","title":"Slug"}}],"responses":{"200":{"description":"Successful Response","content":{"application/json":{"schema":{}}}},"422":{"description":"Validation Error","content":{"application/json":{"schema":{"$ref":"#/components/schemas/HTTPValidationError"}}}}}}},"/p/{slug}/a2a/":{"post":{"summary":"A2A","operationId":"a2a_p__slug__a2a__post","parameters":[{"name":"slug","in":"path","required":true,"schema":{"type":"string","title":"Slug"}}],"responses":{"200":{"description":"Successful Response","content":{"application/json":{"schema":{}}}},"422":{"description":"Validation Error","content":{"application/json":{"schema":{"$ref":"#/components/schemas/HTTPValidationError"}}}}}}},"/p/{slug}/a2a":{"post":{"summary":"A2A","operationId":"a2a_p__slug__a2a_post","parameters":[{"name":"slug","in":"path","required":true,"schema":{"type":"string","title":"Slug"}}],"responses":{"200":{"description":"Successful Response","content":{"application/json":{"schema":{}}}},"422":{"description":"Validation Error","content":{"application/json":{"schema":{"$ref":"#/components/schemas/HTTPValidationError"}}}}}}}},"components":{"schemas":{"HTTPValidationError":{"properties":{"detail":{"items":{"$ref":"#/components/schemas/ValidationError"},"type":"array","title":"Detail"}},"type":"object","title":"HTTPValidationError"},"ProviderTask":{"properties":{"task_id":{"type":"string","title":"Task Id"},"requester_id":{"type":"string","title":"Requester Id"},"document_url":{"type":"string","title":"Document Url","description":"{DOCS_BASE_URL}/d/{doc_id}.pdf (D17)"},"output_schema":{"additionalProperties":true,"type":"object","title":"Output Schema"},"deadline_ms":{"type":"integer","exclusiveMinimum":0.0,"title":"Deadline Ms","description":"Time allowed to answer, in ms from receipt"},"offered_price":{"type":"number","minimum":0.0,"title":"Offered Price","description":"USD offered for this document"}},"additionalProperties":false,"type":"object","required":["task_id","requester_id","document_url","output_schema","deadline_ms","offered_price"],"title":"ProviderTask","description":"Body of POST /p/{slug}/tasks. Identical for trials and real jobs: no trial flag, ever."},"ValidationError":{"properties":{"loc":{"items":{"anyOf":[{"type":"string"},{"type":"integer"}]},"type":"array","title":"Location"},"msg":{"type":"string","title":"Message"},"type":{"type":"string","title":"Error Type"},"input":{"title":"Input"},"ctx":{"type":"object","title":"Context"}},"type":"object","required":["loc","msg","type"],"title":"ValidationError"}}}}
```

## 2026-11-18T21:18:40 — Fetch hired provider mock-b's card

**Request:** `GET http://localhost:8101/p/mock-b/.well-known/agent-card.json`

**Response:** status 200

```
{"slug":"mock-b","name":"Mock B","description":"Test double with a known, documented accuracy (mocks/README.md).","version":"1","job_types":["invoice_extraction"],"price_per_doc_usd":0.003,"region":"global"}
```

## Outcome — stopped, no invoice extracted

- Hire succeeded: task `task-6dd98815b01c`, job `job-416b7e6d011a`, offline mode. Recommended **`mock-b@1`**
  (trial accuracy 0.80, CI [0.80, 0.80] from a 1-document trial, $0.001/doc in the trial; card list price $0.003/doc).
  Runner-up `mock-a@1` scored 0.70. Trial spend $0.0065.
- Blocked at "use the hired agent": the hire result gives only the ID `mock-b@1`, with no endpoint, and Agent-Hunter has no
  method to send real invoices to the hired provider (AGENT_INTERFACE.md §1).
- Undocumented route found by probing: `POST http://localhost:8101/p/{slug}/tasks` (ProviderTask) needs a `document_url` of the form
  `{DOCS_BASE_URL}/d/{doc_id}`. No doc_id is given to me and I can't upload an invoice, so I couldn't build a valid request.
  Stopped there instead of reverse-engineering further.
- Even if that worked, the offline mocks don't read the PDF. They answer from a stored answer key with deliberate corruption
  (§5.7), so any "extraction" would be synthetic.
