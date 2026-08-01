import json

path = r'C:\Users\skell\Desktop\Vault2\vaultbot_stuff\vaultbot_backend\sessions\5df53bfd-2dc2-42a3-bf16-35ea9402f096.jsonl'
lines = open(path, encoding='utf-8').readlines()
print(f"Total lines: {len(lines)}")

# Find key events
for i, line in enumerate(lines):
    try:
        obj = json.loads(line)
        evt = obj.get('event','')
        data = obj.get('data',{})
        if evt in ('prompt_built', 'chat_begin', 'round_loop_top', 'agent_round', 
                    'tool_call_requested', 'tool_call_result', 'chat_end',
                    'no_done_marker_nudge', 'no_done_marker_accepted', 'turn_done_marker'):
            print(f"  [{i}] {evt}: {json.dumps(data)[:200]}")
    except:
        pass

# Extract the last thinking phase (after the second tool result)
thinking = ""
found_second_result = False
for i, line in enumerate(lines):
    try:
        obj = json.loads(line)
        evt = obj.get('event','')
        if evt == 'tool_call_result':
            found_second_result = True
            continue
        if found_second_result:
            payload = obj.get('data',{}).get('payload',{}) if isinstance(obj.get('data',{}), dict) else {}
            if isinstance(payload, dict) and payload.get('type') == 'thinking':
                thinking += payload.get('content','')
    except:
        pass

print(f"\n=== THINKING AFTER 2nd TOOL RESULT: {len(thinking)} chars ===")
print(thinking[:4000])
if len(thinking) > 4000:
    print("...")
    print(thinking[-2000:])