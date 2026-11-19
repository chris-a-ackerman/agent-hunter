"""Wrap a hire body JSON file in a JSON-RPC message/send envelope.
Usage: python3 -I wrap.py <body.json> <out.json> <messageId>"""
import json, sys
body = json.load(open(sys.argv[1]))
env = {"jsonrpc": "2.0", "id": 1, "method": "message/send",
       "params": {"message": {"kind": "message", "role": "user", "messageId": sys.argv[3],
                              "parts": [{"kind": "text", "text": json.dumps(body)}]}}}
json.dump(env, open(sys.argv[2], "w"), indent=1)
