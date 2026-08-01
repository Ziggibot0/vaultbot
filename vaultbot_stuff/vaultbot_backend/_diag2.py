import json

path = r'C:\Users\skell\Desktop\Vault2\vaultbot_stuff\vaultbot_backend\sessions\5df53bfd-2dc2-42a3-bf16-35ea9402f096.jsonl'
lines = open(path, encoding='utf-8').readlines()

# Show events from line 602 onwards
print("=== EVENTS FROM ROUND 3 ONWARD ===")
for i in range(602, len(lines)):
    try:
        obj = json.loads(lines[i])
        evt = obj.get('event','')
        data = obj.get('data',{})
        if evt in ('agent_round', 'tool_call_requested', 'tool_call_result', 
                    'tool_call_invalid', 'chat_end', 'no_done_marker_nudge',
                    'no_done_marker_accepted', 'turn_done_marker', 
                    'agent_silent_fail_loud', 'empty_answer_nudge',
                    'round_loop_top'):
            print(f"  [{i}] {evt}: {json.dumps(data)[:250]}")
    except:
        pass

# Extract thinking + answer from round 3 onward
thinking = ""
answer = ""
for i in range(602, len(lines)):
    try:
        obj = json.loads(lines[i])
        payload = obj.get('data',{}).get('payload',{}) if isinstance(obj.get('data',{}), dict) else {}
        if isinstance(payload, dict):
            if payload.get('type') == 'thinking':
                thinking += payload.get('content','')
            elif payload.get('type') == 'answer_chunk':
                answer += payload.get('content','')
    except:
        pass

print(f"\n=== ROUND 3+ THINKING ({len(thinking)} chars) ===")
print(thinking[:3000])
if len(thinking) > 3000:
    print("...")
    print(thinking[-1500:])

print(f"\n=== ROUND 3+ ANSWER ({len(answer)} chars) ===")
print(answer[:500] if answer else "(none)")