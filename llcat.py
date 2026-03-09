#!/usr/bin/env python3
import sys, requests, json, argparse, subprocess, select, importlib.metadata, traceback, os
import logging

logging.basicConfig(level=(os.environ.get('LOGLEVEL') or 'warning').upper())

VERSION = None
SHUTUP = []
CURLIFY = False

def create_content_with_attachments(text_prompt, attachment_list):
    import base64, re
    content = []
    
    for file_path in attachment_list:
        file_data = safeopen(file_path, what='attachment', fmt='bin')
        ext = os.path.splitext(file_path)[1].lower().lstrip('.')
        prefix = "image" if re.match(r'((we|)bm?p|j?p[en]?g)', ext) else "application"
        
        content.append({
            'type': 'document' if prefix == "application" else "image",
            'source': {
                'type': 'base64',
                'media_type': f"{prefix}/{ext}",
                'data': base64.b64encode(file_data).decode('utf-8')
            }
        })
    
    if text_prompt:
        content.append({
            'type': 'text',
            'text': text_prompt
        })
    
    return content if len(content) > 1 else text_prompt

def maybejson(txt):
    try:
        return json.loads(txt)
    except:
        return txt

def safeopen(path, what='cli', fmt='json', can_create=False):
    try:
        flags = 'rb' if fmt == 'bin' else 'r'

        if(os.path.exists(path)) or can_create:
            if can_create:
                fd = os.open(path, os.O_RDONLY | os.O_CREAT, mode=0o644)
            else:
                fd = os.open(path, os.O_RDONLY)

            with os.fdopen(fd, flags) as f:
                if fmt == 'json':
                    try:
                        return json.load(f)
                    except Exception as ex:
                        if can_create and os.path.getsize(path) == 0:
                            return [] 
                        err_out(what=what, message=f"{path} is unparsable: {ex}", code=2)

                return f.read()

        err_out(what=what, message=f"{path} is an invalid or inaccessible path", code=2)

    except Exception as ex:
        err_out(what=what, message=f"{path} cannot be loaded", obj=traceback.format_exc(), code=126)

def safecall(base_url, req = None, headers = {}, what = "post"):
    headers['User-Agent'] = headers['X-Title'] = 'llcat'
    headers['HTTP-Referer'] = 'https://github.com/day50-dev/llcat'

    try:
        logging.debug(f"request {req}")
        if what == 'post':
            r = requests.post(base_url, json=req, headers=headers, stream=True)
        else:
            r = requests.get(base_url, headers=headers, stream=True)

        if CURLIFY:
            import curlify
            print(curlify.to_curl(r.request), file=sys.stderr)

        r.raise_for_status()  

    except Exception as e:
        obj = {'request': req, 'response': {}}

        if hasattr(e, 'response') and e.response is not None:
            obj['response']['status_code'] = e.response.status_code
            try:
                error_data = e.response.json()
                obj['response']['payload'] = error_data
            except:
                obj['response']['payload'] = e.response.text

        err_out(what='response', message=str(e), obj=obj)
    return r

def mcp_start(server_config):
    """Start MCP server and return (proc, rpc)"""
    sub_env = os.environ.copy()
    sub_env.update(server_config.get('env') or {})

    cmd = [server_config['command']] + server_config['args']
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=sub_env
    )

    id = 0
    def rpc(method, params=None):
        nonlocal id
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            id += 1
            msg["params"] = params
            msg["id"] = id

        proc.stdin.write(json.dumps(msg) + '\n')

    rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "llcat", "version": "1.0"}})
    rpc("notifications/initialized")

    proc.stdin.flush()  

    rlist, _, _ = select.select([proc.stderr, proc.stdout], [], [], 10.0)
    if proc.stderr in rlist:
        err_out(what="toolcall", message=proc.stderr.readline(), obj=cmd)
    if proc.stdout in rlist:
        proc.stdout.readline()

    return proc, rpc

def mcp_finish(proc):
    """Flush, read response, terminate, return parsed JSON"""
    try:
        proc.stdin.flush()
    except:
        pass

    res_json = None
    response = None
    rlist, _, _ = select.select([proc.stdout], [], [], 10.0)

    if rlist:
        response = proc.stdout.readline()
        try:
            res_json = json.loads(response)
        except:
            pass
    else:
        rlist, _, _ = select.select([proc.stderr], [], [], 0.0)
        if proc.stderr in rlist:
            response = proc.stderr.readline()
            proc.terminate()
            err_out(what="toolcall", message=response)

    proc.terminate()
    if res_json:
        return res_json.get('result', {})
    return response

def discover_tools(server_config):
    proc, rpc = mcp_start(server_config)
    rpc("tools/list", {})
    res = mcp_finish(proc)
    if type(res) is str: 
        return res
    return res.get('tools')

def call_tool(server_config, tool_name, arguments):
    if type(arguments) is str:
        arguments = json.loads(arguments)

    proc, rpc = mcp_start(server_config)
    rpc("tools/call", {"name": tool_name, "arguments": arguments})
    return mcp_finish(proc)

mcp_dict_ref = {}
def mcp_get_def(path):
    import re
    config = safeopen(path)

    global mcp_dict_ref
    tool_return = []
    for server_name, server_config in config.get('mcpServers').items():
        if server_config.get("disabled"):
            continue
        safe_name = re.sub(r'[^a-z0-9_]', '_', server_name.lower())
        counter = 0
        
        tool_dict = discover_tools(server_config)
        for tool in tool_dict:
            base_name = f"{safe_name}_{tool['name']}"
            llm_tool_name = base_name
            
            while llm_tool_name in mcp_dict_ref:
                llm_tool_name = f"{base_name}{counter}"
                counter += 1
            
            mcp_dict_ref[llm_tool_name] = (server_config, tool['name'])
            tool['name'] = llm_tool_name
            tool['parameters'] = tool['inputSchema']
            del tool['inputSchema']

            tool_return.append({'type': 'function', 'function': tool})

    return tool_return
        
def err_out(what="general", message="", obj=None, code=1):
    if not set(['error',what]).intersection(SHUTUP):
        fulldump={'data': obj, 'level': 'error', 'class': what, 'message': message}
        print(json.dumps(fulldump), file=sys.stderr)
    sys.exit(code)

def tool_gen(res):
    for line in res.iter_lines():
        if line:
            line = line.decode('utf-8')
            logging.debug(f"response: {line}")
            if line.startswith('data: '):
                data = line[6:]
                if data == '[DONE]':
                    break
                yield data

def main():
    global CURLIFY, VERSION, mcp_dict_ref 

    try:
        VERSION = importlib.metadata.version('llcat')
    except:
        VERSION = "git"

    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""
llcat is /usr/bin/cat for LLMs. 

        🐱 Me-wow! 

https://github.com/day50-dev/llcat""")

    # We want to show things in the order of importance
    parser.add_argument('-su', '-u', '--server_url', help='Server URL (e.g., http://::1:8080). Also supports MSA format')
    parser.add_argument('-sk', '-k', '--server_key', help='Server API key for authorization')

    parser.add_argument('-m',  '--model', nargs='?', const='', default='any', help='Model to use (or list models if no value)')
    parser.add_argument('-s',  '--system', help='System prompt')

    parser.add_argument('-c',  '--conversation', help='Conversation history file')
    parser.add_argument('-cr', action='store_true', help="Do not write anything back to the conversation file")
    parser.add_argument('-mf', '--mcp_file', help='MCP file to use')
    parser.add_argument('-tf', '--tool_file', help='JSON file with tool definitions')
    parser.add_argument('-tp', '--tool_program', help='Program to execute tool calls')
    parser.add_argument('-a',  '--attach', action='append', help='Attach file(s)')
    parser.add_argument('-bq', '--be_quiet', action='append', help='Make it shutup about things')
    parser.add_argument('-nw', '--no_wrap', action='store_true', help='Do not wrap inputs in <xml-like-syntax>')
    parser.add_argument('--curlify', action='store_true', help="Write curl equivalents of calls to stdout")
    parser.add_argument('--version', action='version', version='%(prog)s ' + VERSION)
    parser.add_argument('--info', nargs='?', const='caps', help='Get the info for a model')
    parser.add_argument('user_prompt', nargs='*', help='Your prompt')
    args = parser.parse_args()

    if args.curlify:
        CURLIFY = True

    if args.be_quiet:
        global SHUTUP
        SHUTUP = set((','.join(args.be_quiet)).split(','))

    # Server and headers
    if args.server_url:

        # MAS support (https://day50.dev/mas.html)
        if '#' in args.server_url:
            from urllib.parse import parse_qs, parse_qsl
            lhs, rhs = args.server_url.split('#')
            params = parse_qs(rhs, keep_blank_values=True)
            args.model = params.get('m')[0]
        else:
            lhs = args.server_url

        base_url = lhs.rstrip('/').removesuffix('/v1')

    headers = {'Content-Type': 'application/json'}
    if args.server_key:
        headers['Authorization'] = f'Bearer {args.server_key}'

    # Prompt 
    cli_prompt = ' '.join(args.user_prompt) if args.user_prompt else ''
    stdin_prompt = sys.stdin.read() if select.select([sys.stdin], [], [], 0.0)[0] else ''

    if (not args.no_wrap) and len(stdin_prompt) and len(cli_prompt):
        prompt = f"<ask>{cli_prompt}</ask><content>{stdin_prompt}</content>"
    else:
        if len(cli_prompt):
            cli_prompt += "\n"
        prompt = cli_prompt + stdin_prompt
    
    if not args.server_url:
        if len(prompt) == 0:
            parser.print_help()
        else:
            print(prompt)
        sys.exit(0)

    # Model
    if not args.model or (len(prompt) == 0 and not args.conversation):
        r = safecall(base_url=f'{base_url}/v1/models', headers=headers, what='get')

        try:
            resp = r.json()
            models = resp.get('data') or resp.get('models')
            
            for model in models:
                if args.model == '':
                    print(model['id'])
                elif args.model in [model['id'], '*']:
                    params = model.get('supported_parameters')
                    if params:
                        if args.info:
                            print(json.dumps(params))
                        else:
                            print(json.dumps(model))
                        sys.exit(0)

            if args.model != '':
                r = safecall(base_url=f'{base_url}/api/show', req={"model":args.model}, headers=headers)
                if args.info:
                    print(json.dumps(r.json().get('capabilities')))
                else:
                    print(json.dumps(r.json()))

            sys.exit(0)
        except Exception as ex:
            err_out(what="parsing", message=f"{base_url}/models is unparsable json: {ex}", obj=r.text, code=126)


    # Conversation
    messages = safeopen(args.conversation, can_create=True) if args.conversation else []

    # Tools
    tools = None
    if args.tool_file:
        tools = safeopen(args.tool_file)
        for tool in tools:
            # we demand the tool program to be executable
            mcp_dict_ref[tool['function']['name']] = ({'command':args.tool_program,'args':[]}, tool['function']['name'])

    if args.mcp_file:
        tools = tools or []
        tools += mcp_get_def(args.mcp_file)

    # Attachment
    message_content = create_content_with_attachments(prompt, args.attach) if args.attach else prompt

    # System Prompt
    if args.system:
        if len(messages) > 0: 
            if messages[0].get('role') != 'system':
                messages.insert(0, {})
            messages[0] = {'role': 'system', 'content': args.system}
        else:
            messages.append({'role': 'system', 'content': args.system})

    messages.append({'role': 'user', 'content': message_content})

    # Request construction
    req = {
        'model': args.model,
        'messages': messages, 
        'stream': True
    }

    if tools:
        req['tools'] = tools

    # The actual call
    assistant = {
        'content': '',
        'reasoning': '',
        'tool_calls': []
    }

    while True:
        r = safecall(f'{base_url}/v1/chat/completions',req,headers)
        tool_call_list = []

        is_thinking = False
        for data in tool_gen(r):
            try:
                chunk = json.loads(data)
                delta = chunk['choices'][0]['delta']
                content = delta.get('content', '') 
                reasoning = delta.get('reasoning', delta.get('reasoning_content', ''))
                tool_calls = delta.get('tool_calls', [])

                if len(reasoning.strip()) and not 'think' in SHUTUP:
                    if not is_thinking:
                        print("<think>")
                        is_thinking = True

                    assistant['reasoning'] += reasoning
                    print(reasoning, end='', flush=True)

                elif content:
                    if is_thinking:
                        print("\n</think>")
                        is_thinking = False

                    print(content, end='', flush=True)
                    assistant['content'] += content
                
                for tc in tool_calls:
                    idx = tc.get('index', 0)
                    if idx >= len(tool_call_list):
                        tool_call_list.append({'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
                    
                    if 'id' in tc:
                        tool_call_list[idx]['id'] = tc['id']
                    if 'function' in tc:
                        for key in ['name','arguments']:
                            if key in tc['function']:
                                tool_call_list[idx]['function'][key] += tc['function'][key]

            except Exception as ex:
                err_out(what="toolcall", message=traceback.format_exc(), obj=data)

        for tool_call in tool_call_list:
            fname = tool_call['function']['name']
            
            if not set(['toolcall','debug','request']).intersection(SHUTUP):
                print(json.dumps({'level':'debug', 'class': 'toolcall', 'message': 'request', 'obj': tool_call}), file=sys.stderr)
            
            if args.tool_program and '/' not in args.tool_program:
                args.tool_program = './' + args.tool_program

            if fname not in mcp_dict_ref:
                err_out(what="toolcall", message=f"{fname} is not a tool")

            config, name = mcp_dict_ref[fname]
            result = json.dumps( call_tool(config, name, tool_call['function']['arguments']))

            if not set(['toolcall','debug','result']).intersection(SHUTUP):
                print(json.dumps({'level':'debug', 'class': 'toolcall', 'message': 'result', 'obj': maybejson(result)}), file=sys.stderr)
            
            messages.append({
                'role': 'tool',
                'name': fname,
                'tool_call_id': tool_call['id'],
                'content': result
            })
        
        req = {'messages': messages, 'stream': True}
        if args.model:
            req['model'] = args.model
        if tools:
            req['tools'] = tools

        if len(tool_call_list) == 0:
            break

    if args.conversation and not args.cr:
        do_append = False
        newline = {'role': 'assistant'}
        #print(newline)
        for k,v in assistant.items():
            if len(v):
                newline[k] = v
                do_append = True

        if do_append:
            messages.append(newline)
            try:
                with open(args.conversation, 'w') as f:
                    json.dump(messages, f, indent=2)
            except Exception as ex:
                err_out(what="conversation", message=f"{args.conversation} is unwritable", obj=traceback.format_exc(), code=126)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt as ex:
        err_out(message=f"Keyboard interrupt")
    #except Exception as ex:
    #    err_out(message=traceback.format_exc()
