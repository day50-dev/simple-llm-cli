#!/usr/bin/env python3
import sys, json, argparse, subprocess, os

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
    parser = argparse.ArgumentParser(description='Inject a tool result into a conversation JSON file')
    parser.add_argument('file', help='conversation JSON file (read/write)')
    parser.add_argument('--name', required=True, help='tool name')
    parser.add_argument('--tool-call-id', required=True, help='tool call ID')
    parser.add_argument('--tool-call', help='tool_call JSON object (auto-generated from name/id if omitted)')
    parser.add_argument('--content', help='result content string')
    parser.add_argument('--run', help='command to execute; its stdout becomes the content')
    parser.add_argument('--offset', type=int, default=-1, help='insertion position: -1 = end (default), 0 = after system prompt, N = at index N')
    args = parser.parse_args()

    content = args.content
    if args.run:
        try:
            result = subprocess.run(args.run, shell=True, capture_output=True, text=True, check=False)
            content = result.stdout
        except Exception as ex:
            err_out(what='toolcall', message=f'--run command failed: {ex}', code=126)

    if content is None:
        err_out(what='toolcall', message='either --content or --run is required', code=2)

    tool_call = maybejson(args.tool_call) if args.tool_call else {
        'id': args.tool_call_id,
        'type': 'function',
        'function': {'name': args.name, 'arguments': '{}'}
    }

    try:
        with open(args.file, 'r') as f:
            messages = json.load(f)
    except Exception as ex:
        err_out(what='toolcall', message=f'{args.file} is unparsable: {ex}', code=2)

    if not isinstance(messages, list):
        err_out(what='toolcall', message=f'{args.file} must contain a JSON array', code=2)

    entry = {
        'role': 'tool',
        'name': args.name,
        'tool_call_id': args.tool_call_id,
        'tool_call': tool_call,
        'content': content
    }

    offset = args.offset
    if offset < 0 or offset >= len(messages):
        messages.append(entry)
    else:
        if offset == 0 and len(messages) and messages[0].get('role') == 'system':
            offset = 1
        messages.insert(offset, entry)

    with open(args.file, 'w') as f:
        json.dump(messages, f, indent=2)

if __name__ == "__main__":
    main()
