#!/usr/bin/env python3
import sys, json, argparse, subprocess, os, uuid

def maybejson(txt):
    try:
        return json.loads(txt)
    except:
        return txt

def err_out(what="general", message="", obj=None, code=1):
    fulldump={'data': obj, 'level': 'error', 'class': what, 'message': message}
    print(json.dumps(fulldump), file=sys.stderr)
    sys.exit(code)

def main():
    parser = argparse.ArgumentParser(description='Inject a tool result into a conversation JSON file. Part of llcat (https://github.com/day50-dev/llcat)')
    parser.add_argument('file', help='conversation JSON file (read/write) or output file (with --run)')
    parser.add_argument('--name', help='tool name')
    parser.add_argument('--toolcall-id', '-ti', help='tool call ID')
    parser.add_argument('--toolcall', '-tc', help='tool_call JSON object (auto-generated from name/id if omitted)')
    parser.add_argument('--content', help='result content string')
    parser.add_argument('--run', '-r', help='command to execute; its stdout becomes the content (writes file directly)')
    parser.add_argument('--offset', type=int, default=-1, help='insertion position: -1 = end (default), 0 = after system prompt, N = at index N')
    args = parser.parse_args()

    content = args.content
    if args.run:
        try:
            result = subprocess.run(args.run, shell=True, capture_output=True, text=True, check=False)
            content = result.stdout
        except Exception as ex:
            err_out(what='toolcall', message=f'--run command failed: {ex}', code=126)
        try:
            with open(args.file, 'r') as f:
                messages = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            messages = []

        name = args.name or '[REDACTED]'
        toolcall_id = args.toolcall_id or f"id-{uuid.uuid4().hex}"
        tool_call = maybejson(args.toolcall) if args.toolcall else {
            'id': toolcall_id,
            'type': 'function',
            'function': {'name': name, 'arguments': json.dumps({'command': args.run})}
        }
        messages.append({
            'role': 'assistant',
            'content': None,
            'tool_calls': [tool_call]
        })
        messages.append({
            'role': 'tool',
            'name': name,
            'tool_call_id': toolcall_id,
            'content': content
        })
        with open(args.file, 'w') as f:
            json.dump(messages, f, indent=2)
        return

    if content is None:
        err_out(what='toolcall', message='either --content or --run is required', code=2)

    name = args.name or '[REDACTED]'
    toolcall_id = args.toolcall_id or f"id-{uuid.uuid4().hex}"
    tool_call = maybejson(args.toolcall) if args.toolcall else {
        'id': toolcall_id,
        'type': 'function',
        'function': {'name': name, 'arguments': '{}'}
    }

    try:
        with open(args.file, 'r') as f:
            messages = json.load(f)
    except Exception as ex:
        err_out(what='toolcall', message=f'{args.file} is unparsable: {ex}', code=2)

    if not isinstance(messages, list):
        err_out(what='toolcall', message=f'{args.file} must contain a JSON array', code=2)

    assistant_entry = {
        'role': 'assistant',
        'content': None,
        'tool_calls': [tool_call]
    }
    tool_entry = {
        'role': 'tool',
        'name': name,
        'tool_call_id': toolcall_id,
        'content': content
    }

    offset = args.offset
    entries = [assistant_entry, tool_entry]
    if offset < 0 or offset >= len(messages):
        messages.extend(entries)
    else:
        if offset == 0 and len(messages) and messages[0].get('role') == 'system':
            offset = 1
        messages[offset:offset] = entries

    with open(args.file, 'w') as f:
        json.dump(messages, f, indent=2)

if __name__ == "__main__":
    main()
