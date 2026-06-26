#!/usr/bin/env python3
import sys, requests, json, argparse, subprocess, select, importlib.metadata, traceback, os
import logging

logging.basicConfig(level=(os.environ.get('LOGLEVEL') or 'warning').upper())

VERSION = None
SHUTUP = []
CURLIFY = False
DRY = False
TIMEOUT = None
SESSION = None

mcp_dict_ref = {}

def create_content_with_attachments(text_prompt, attachment_list):
    import base64, re
    content = []
    
    for file_path in attachment_list:
        file_data = safeopen(file_path, what='attachment', fmt='bin')
        ext = os.path.splitext(file_path)[1].lower().lstrip('.')
        prefix = "image" if re.match(r'((we|)bm?p|j?p[en]?g)', ext) else "application"
        b64 = 'data:image/png;base64,' + base64.b64encode(file_data).decode('utf-8')
        
        content.append({
            'type': 'image_url', #'document' if prefix == "application" else "image",
            'image_url': b64
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
    global SESSION
    headers['User-Agent'] = headers['X-Title'] = 'llcat'
    headers['HTTP-Referer'] = 'https://github.com/day50-dev/llcat'

    try:
        logging.debug(f"request {req}")
        
        req_kwargs = {
            'method': what.upper(),
            'url': base_url,
            'headers': headers,
        }
        if what == 'post':
            req_kwargs['json'] = req
            
        req_obj = requests.Request(**req_kwargs)
        try:
            prepared = SESSION.prepare_request(req_obj)
        except:
            SESSION = requests.Session()
            prepared = SESSION.prepare_request(req_obj)

        if CURLIFY:
            import curlify
            print(curlify.to_curl(prepared), file=sys.stderr)

        if DRY:
            sys.exit(0)

        r = SESSION.send(prepared, stream=True, timeout=TIMEOUT)
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

        if SESSION is not None:
            try:
                SESSION.close()
            except:
                pass

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

    while True:
        rlist, _, _ = select.select([proc.stderr, proc.stdout], [], [], 10.0)
        if proc.stderr in rlist:
            err_out(what="toolcall", message=proc.stderr.readline(), obj=cmd)
            continue

        if proc.stdout in rlist:
            proc.stdout.readline()
        break

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
        obj_json = obj
        try:
            if isinstance(obj, str):
                obj_json = json.dumps(obj)
        except:
            obj_json = obj
        fulldump={'data': obj_json, 'level': 'error', 'class': what, 'message': message, 'tb': traceback.format_exc()}

        print(json.dumps(fulldump), file=sys.stderr)
    sys.exit(code)

def model_info(args, base_url, headers):
    r = safecall(base_url=f'{base_url}/v1/models', headers=headers, what='get')
    res = []
    splat = False

    try:
        resp = r.json()
        models = resp.get('data') or resp.get('models')

        if '*' in args.model:
            import fnmatch
            splat = True
        
        for model in models:
            if '*' in args.model and not fnmatch.fnmatch(model.get('id'), args.model):
                continue

            if args.info or (args.model in [model['id'], '*'] and len(model['id'])):
                params = model.get('supported_parameters')
                if not params:
                    r = safecall(base_url=f'{base_url}/api/show', req={"model":model.get('id')}, headers=headers)
                    model_info = r.json()
                    params = model_info.get('capabilities')
                else:
                    model_info = model

                if args.info:
                    res.append({'model': model['id'], 'supported_parameters': params})
                else:
                    res.append(model_info)

            elif splat or args.model == '':
                print(model['id'])


        if len(res):
            print(json.dumps(res))

        sys.exit(0)

    except Exception as ex:
        err_out(what="parsing", message=f"{base_url}/models is unparsable json: {ex}", obj=r.text, code=126)


def tool_gen(res):
    for line in res.iter_lines():
        if line:
            line = line.decode('utf-8')
            logging.debug(f"response: {line}")
            if line.startswith('data: '):
                data = line[6:]
                if data == '[DONE]':
                    break
                yield json.loads(data)

def stringfile(instr):
    res = instr
    if instr[0] == '@':
        if os.path.exists(instr[1:]):
            with open(instr[1:], 'r') as f:
                res = f.read().strip()
        else:
            logging.warning(f"{instr} specified, it uses file syntax, however the file doesn't exist. Using it as a string.")

    return res

def base_request(args):
    try:
        eb = json.loads(stringfile(args.extra_body))
    except Exception as ex:
        err_out(what="parsing", message=f"{args.extra_body} is unparsable json: {ex}", code=126)

    req = {
        'model': args.model,
        'stream': not args.no_stream,
        **eb
    }

    if args.no_think:
        # There's no universal way to do this, let's just hope this doesn't
        # break anything. *shrug*

        # This is OpenAI's version.
        # For models > 5, none is supported. Otherwise it's "low". 
        # Importantly LiteLLM (https://docs.litellm.ai/docs/reasoning_content) uses "low"
        if args.proto in ('auto', 'openai'):
            req['reasoning_effort'] = 'low'
        
        # OpenRouter does it their own way: https://openrouter.ai/docs/guides/best-practices/reasoning-tokens
        if args.proto == 'openrouter' or 'openrouter.ai' in args.server_url:
            req['reasoning'] = {
                'effort': 'none',
                'max_tokens': 0,
                'exclude': True,
                'enabled': False
            }

        # as does ollama https://ollama.com/blog/thinking
        if args.proto in ('auto', 'ollama'):
            req['think'] = False

        # llama.cpp, vllm, and sglang use this syntax: https://github.com/ggml-org/llama.cpp/issues/20196
        if args.proto in ('auto', 'llama.cpp', 'vllm', 'sglang'):
            req['chat_template_kwargs'] = {
                'enable_thinking': False
            }

    # schema construction
    if args.schema:
        req['response_format'] = {
            'type': 'json_schema',
            'json_schema': json.loads(stringfile(args.schema))
        }

    return req

def main():
    global SHUTUP, CURLIFY, VERSION, DRY, TIMEOUT, mcp_dict_ref 

    try:
        VERSION = importlib.metadata.version('llcat')
    except:
        VERSION = "git"

    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""
llcat is /usr/bin/cat for LLMs. 

        🐱 Me-wow! 

https://github.com/day50-dev/llcat

Options with a [@] prefix can either be strings or paths to a file, curl style, @/like/this.
""")

    # We want to show things in the order of importance
    parser.add_argument('-su', '-u', '--server_url',        help='server URL (e.g., http://::1:8080). Also supports MAS format')
    parser.add_argument('-sk', '-k', '--server_key',        metavar='[@]SERVERKEY', help='server API key for authorization')
    parser.add_argument('-to', '--timeout', type=float,     help='timeout in seconds for the read')
    parser.add_argument('-pr', '--proto', default='auto',   help='protocol to use (ollama, llama.cpp, openai, auto)')

    parser.add_argument('-m',  '--model', nargs='?', const='', default='any', help='model to use (or list models if no value)')
    parser.add_argument('-s',  '--system', metavar='[@]SYSTEM', help='system prompt')
    parser.add_argument('-a',  '--attach', action='append', help='attach file(s)')

    parser.add_argument('-c',  '--conversation',    help='conversation history file (r/w)')
    parser.add_argument('-cr', '--conversationro',  help="the readonly conversation input (ro)")

    parser.add_argument('-eb', '--extra_body',  metavar='[@]EXTRABODY', default='{}', help='JSON to add to the body, such as max_tokens or temperature')
    parser.add_argument('-sc', '--schema',      metavar='[@]SCHEMA', help='set a schema to force structured output')
    parser.add_argument('-mf', '--mcp_file',    help='MCP file to use')
    parser.add_argument('-tp', '--tool_program', help='program to execute tool calls')
    parser.add_argument('-tf', '--tool_file',   help='JSON file with tool definitions')

    parser.add_argument('-ps', '--ps',       action='store_true', help='currently running model (if supported)')
    parser.add_argument('-bq', '--be_quiet', action='append',     help='make it shutup about things')
    parser.add_argument('-nt', '--no_think', action="store_true", help='disable thinking')
    parser.add_argument('-ns', '--no_stream',action="store_true", help='disable streaming')
    parser.add_argument('-nw', '--no_wrap',  action='store_true', help='do not wrap inputs in <xml-like-syntax>')
    parser.add_argument('--curlify',         action='store_true', help="write curl equivalents of calls to stdout")
    parser.add_argument('--dry',             action='store_true', help="dry run")
    parser.add_argument('--version',         action='version', version='%(prog)s ' + VERSION)
    parser.add_argument('--info', nargs='?', const='caps', help='get the info for a model')
    parser.add_argument('user_prompt', nargs='*', help='your prompt')
    args = parser.parse_args()

    if args.curlify:  CURLIFY = True
    if args.dry:      DRY = True
    if args.be_quiet: SHUTUP = set((','.join(args.be_quiet)).split(','))
    TIMEOUT = args.timeout
    base_url = None

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
        if "//" not in base_url: 
            if 'localhost' in base_url:
                base_url = "http://" + base_url
            else:
                base_url = "https://" + base_url

    headers = {
        'Accept': 'text/event-stream' if not args.no_stream else 'application/json',
        'Content-Type': 'application/json'
    }

    if args.server_key:
        headers['Authorization'] = f'Bearer {stringfile(args.server_key)}'

    if args.ps:
        res = safecall(base_url=f'{args.server_url}/api/ps', headers=headers, what="get")
        if res:
            try:
                res_json = res.json()
            except Exception as e:
                err_out(what='response', message=str(e))

            print(res.json().get('models'))
        sys.exit(0)

    # Model
    if not args.model:
        if not base_url:
            err_out(what="invocation", message="base_url not specified. Cannot continue")

        model_info(args, base_url, headers)

    # Prompt 
    # 
    # It's worth noting that we do the prompt AFTER the model because
    # it will suck stdin. If someone is doing a models query it shouldn't
    # consume the stdin tokens.
    #
    cli_prompt = ' '.join(args.user_prompt) if args.user_prompt else ''
    stdin_prompt = sys.stdin.read() if select.select([sys.stdin], [], [], 0.0)[0] else ''

    if (not args.no_wrap) and len(stdin_prompt) and len(cli_prompt):
        prompt = f"<ask>{cli_prompt}</ask><content>{stdin_prompt}</content>"
    else:
        if len(cli_prompt) and len(stdin_prompt):
            cli_prompt += "\n"
        prompt = cli_prompt + stdin_prompt
    
    if not args.server_url:
        parser.print_help() if len(prompt) == 0 else print(prompt)
        sys.exit(0)

    if len(prompt) == 0 and not args.conversation:
        model_info(args, base_url, headers)

    # Conversation
    convo_file = args.conversationro or args.conversation or None
    messages = safeopen(convo_file, can_create=True) if convo_file else []

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
        payload = {'role': 'system', 'content': stringfile(args.system)}
        if len(messages) > 0: 
            if messages[0].get('role') != 'system':
                messages.insert(0, {})
            messages[0] = payload
        else:
            messages.append(payload)

    messages.append({'role': 'user', 'content': message_content})

    req = base_request(args)
    req['messages'] = messages

    if tools:
        req['tools'] = tools

    # The actual call
    assistant = {
        'content': '',
        'reasoning': '',
        'tool_calls': []
    }

    try:
        while True:
            r = safecall(f'{base_url}/v1/chat/completions', req, headers)
            tool_call_list = []

            is_thinking = False
            for chunk in tool_gen(r):
                try:
                    if 'choices' not in chunk:
                        err_out(what="parser", message="Unparsable content", obj={'req':req, 'res':chunk})

                    # nvidia's inference does things in a weird way
                    if len(chunk['choices']) == 0 or chunk['choices'][0]['finish_reason'] == 'stop':
                        break

                    delta = chunk['choices'][0]['delta']

                    content = delta.get('content', '') 
                    reasoning = delta.get('reasoning', delta.get('reasoning_content', '')) or ''
                    tool_calls = delta.get('tool_calls', [])

                    if (len(assistant.get('reasoning', '')) > 0 or len(reasoning.strip())) and not 'think' in SHUTUP and reasoning:
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
                    
                    if tool_calls:
                        for tc in tool_calls:
                            idx = tc.get('index', 0)
                            if idx >= len(tool_call_list):
                                tool_call_list.append({'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
                            
                            if 'id' in tc:
                                tool_call_list[idx]['id'] = tc['id']
                            if 'function' in tc:
                                for arg in ['name', 'arguments']:
                                    if arg in tc['function']:
                                        tool_call_list[idx]['function'][arg] += tc['function'][arg]

                except Exception as ex:
                    err_out(what="toolcall", message=traceback.format_exc(), obj=req)

            for tc in tool_call_list:
                value = tc['function']['arguments']
                if isinstance(value, str):
                    try:
                        value = json.loads(value)
                    except json.decoder.JSONDecodeError as ex:
                        value = tc['function']['arguments']

                tc['function']['arguments'] = value

            messages.append({
                'role': 'assistant',
                # 'content': assistant.get('content') or json.dumps(tool_call_list),
                'tool_calls': tool_call_list
            })

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
            
            req = base_request(args)
            req['messages'] = messages
            if tools:
                req['tools'] = tools

            if len(tool_call_list) == 0:
                break

        if args.conversation:
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

    except KeyboardInterrupt as ex:
        err_out(message=f"Keyboard interrupt")

if __name__ == "__main__":
    main()
