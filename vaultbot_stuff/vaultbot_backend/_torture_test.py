"""
Torture test: send a real chat message through the VaultBot backend
WebSocket and capture the full session log to verify the model works.
"""
import asyncio
import json
import time
import websockets

BACKEND_URL = "ws://127.0.0.1:8000/ws"
TEST_MESSAGE = "what procedures do you have? list them briefly"

async def torture_test():
    print(f"Connecting to {BACKEND_URL}...")
    async with websockets.connect(BACKEND_URL) as ws:
        print(f"Connected. Sending: {TEST_MESSAGE!r}")
        print(f"Model: qwen3.6:27b (27.8B dense, 5120-dim embeddings)")
        print()

        await ws.send(json.dumps({
            "type": "chat",
            "message": TEST_MESSAGE,
            "model": "qwen3.6:27b"
        }))

        events = []
        start = time.time()
        timeout = 600  # 10 minutes — dense 27B model is slow

        while True:
            try:
                remaining = timeout - (time.time() - start)
                if remaining <= 0:
                    print(f"\n\nTIMEOUT after {timeout}s")
                    break

                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                msg = json.loads(raw)
                events.append(msg)

                mtype = msg.get("type", "")

                if mtype == "status":
                    print(f"  STATUS: {msg.get('content','')}")
                elif mtype == "answer_chunk":
                    print(f"  CHUNK: {msg.get('content','')}", end="", flush=True)
                elif mtype == "tool_call":
                    print(f"\n  TOOL_CALL: {msg.get('tool','')} args={str(msg.get('args',''))[:100]}")
                elif mtype == "tool_result":
                    print(f"  TOOL_RESULT: {msg.get('summary','')}")
                elif mtype == "answer_done":
                    content = msg.get("content", "")
                    has_done = "<done>" in content
                    print(f"\n\n  ANSWER_DONE ({len(content)} chars, <done>={'YES' if has_done else 'NO'}):")
                    print(f"  {content[:500]}")
                    break
                elif mtype == "problem":
                    print(f"\n  PROBLEM: {json.dumps(msg)[:400]}")
                    break
                elif mtype == "context_usage":
                    print(f"  CONTEXT: {msg.get('used_tokens','?')}/{msg.get('context_window','?')} tokens, {msg.get('messages','?')} msgs, model={msg.get('model','?')}")
                elif mtype == "session_info":
                    print(f"  SESSION: {msg.get('session_id','')[:12]}... title={msg.get('title','')[:50]}")
                elif mtype == "progress":
                    detail = str(msg.get("detail", ""))[:80]
                    print(f"  PROGRESS: {msg.get('stage','')} - {detail}")
                elif mtype == "stopped":
                    print(f"\n  STOPPED")
                    break

            except asyncio.TimeoutError:
                print(f"\nTIMEOUT waiting for response")
                break
            except Exception as e:
                print(f"\nERROR: {e}")
                break

        elapsed = time.time() - start
        print(f"\n{'='*60}")
        print(f"Test complete: {len(events)} events in {elapsed:.1f}s")

        thinking_chunks = sum(1 for e in events if e.get("type") == "thinking")
        answer_chunks = sum(1 for e in events if e.get("type") == "answer_chunk")
        tool_calls = sum(1 for e in events if e.get("type") == "tool_call")
        tool_results = sum(1 for e in events if e.get("type") == "tool_result")
        has_answer = any(e.get("type") == "answer_done" for e in events)
        has_problem = any(e.get("type") == "problem" for e in events)

        print(f"  Thinking chunks: {thinking_chunks}")
        print(f"  Answer chunks: {answer_chunks}")
        print(f"  Tool calls: {tool_calls}")
        print(f"  Tool results: {tool_results}")
        print(f"  Answer delivered: {has_answer}")
        print(f"  Problem reported: {has_problem}")

        if has_answer:
            answer = next(e.get("content","") for e in events if e.get("type") == "answer_done")
            print(f"  <done> in answer: {'<done>' in answer}")
            print(f"  Answer length: {len(answer)}")

asyncio.run(torture_test())