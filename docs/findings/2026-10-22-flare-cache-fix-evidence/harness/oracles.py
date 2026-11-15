"""Independent exact checks, stricter than permissive preflight smoke/JSON checks."""
import json

def check(kind, message, expected):
    try:
        text = message.get('content') or ''
        if kind == 'strict':
            return text.strip() == expected
        if kind == 'json':
            return json.dumps(json.loads(text), sort_keys=True) == json.dumps(expected, sort_keys=True)
        if kind == 'tool':
            calls = message.get('tool_calls') or []
            return (len(calls) == 1 and bool(calls[0].get('id'))
                    and calls[0].get('type') == 'function'
                    and calls[0]['function']['name'] == expected['name']
                    and json.loads(calls[0]['function']['arguments']) == expected['arguments'])
        if kind == 'restore_tokens':
            # Numerical fixture, not semantic self-evaluation. Exact stable token sequence required.
            return bool(expected) and message['token_ids'] == expected
        return False
    except (KeyError, TypeError, ValueError):
        return False
