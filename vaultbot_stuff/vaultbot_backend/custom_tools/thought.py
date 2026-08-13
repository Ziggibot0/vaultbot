"""
Agent-authored tool: thought
"""

SCHEMA = {
    "name": "thought",
    "description": "Tool for internal reasoning and reflection. Use this to think through problems, plan steps, or evaluate your state before taking external action. Unlike other tools, calling 'thought' does NOT trigger a premature loop termination or speech trap detection. It is the designated safe space for LLM cognition.",
    "parameters": {
        "properties": {
            "content": {"description": "The internal thought content", "type": "string"}
        },
        "required": ["content"],
        "type": "object",
    },
}


def run(args: dict) -> dict:
    """
    Tool for internal reasoning and reflection. Use this to think through problems,
    plan steps, or evaluate your state before taking external action.

    Unlike other tools, calling 'thought' does NOT trigger a premature loop termination
    or speech trap detection. It is the designated safe space for LLM cognition.
    """
    thought_content = args.get("content", "")
    return {"status": "success", "message": f"Thought logged: {thought_content}"}
