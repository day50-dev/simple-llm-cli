<p align="center">
<img width="238" alt="llcat" src="https://github.com/user-attachments/assets/c161862d-8a8e-4753-a6eb-8a3b67f760b0" />
<br/> <strong>/usr/bin/cat for LLMs</strong>
<br/> <a href=https://pypi.org/project/llcat><img src=https://badge.fury.io/py/llcat.svg/></a>
</p>
<hr>

You want to test if an inference endpoint is working or want to one-shot call a model on a server. Maybe you want to cycle through keys or models or benchmark a bank of IPs. Perhaps you want to orchestrate `N` queries across `M` models running on `P` servers and want to run the job in parallel without leaving any leaky state behind.

Existing tools require you to pick from a provider boutique and a small list of models then swap around credentials like you're Indiana Jones with a bag of sand.

**llcat** is a response to the inconsistent patchwork of tools that sacrifice control for convenience and forfeit functionality.

For instance, let's say I have a list of authentication tokens in some file, `credentials.txt`:

```shell
sk-or-v1-e1e5...
sk-or-v1-ej24...
sk-or-v1-ff24...
```
Here's how you do that with llcat:

```shell
Method 1:

llcat -k @credentials.txt:0
llcat -k @credentials.txt:1
llcat -k @credentials.txt:2

Method 2:

llcat -k sk-or-v1-e1e5...
llcat -k sk-or-v1-ej24...
llcat -k sk-or-v1-ff24...
```

You can do the same pattern with models, system prompts, queries, and servers. For instance:

```shell
llcat -k "@$HOME/credentials.txt:12" \
      -u "@settings.json:.[3].host" \
      -s "@system_prompts:8" \
      -m "@settings.json:.[3].model" \
         "@query.txt:12" > output.txt
```

**Wait wait wait, is that jq?**

Yes! You can use normal strings (ex: `"abc"`), files (ex: `@abc.txt`) with line numbers (ex: `@abc.txt:1`) and even `jq` syntax (ex: `@abc.json:.server[0].url`).

**llcat** is part of the [DAY50](https://day50.dev) suite of open-source tools built for a future where AI workloads are split across devices, private servers, and cloud APIs.
   
`llcat` works through regular JSON files through a principle of "least magic" - prioritizing predictability, compatibility, coherency, transparency and functionality.

It exists as a general-purpose CLI-based OpenAI-compatible `/chat/completions` caller (and also works with Ollama, Openrouter, sglang, llama.cpp ...) 

It is like cURL or cat for LLMs: a stateless, transparent, explicit, low-level, composable tool for scripting and glue.

Conversations, keys, servers and other configurations are explicitly specified each execution as command line arguments. 

This makes building things with llcat direct.

There is no caching or state saved between runs. Everything gets surfaced and errors are JSON parsable. There's a `--curlify` option as well. 

## Very Quick Start
List the models on [OpenRouter](https://openrouter.ai):

`uvx llcat -u openrouter.ai/api -m`

What about just the qwen ones?

`uvx llcat -u openrouter.ai/api -m '*qwen3*'`

What about their capabilities in JSON?

`uvx llcat -u openrouter.ai/api -m '*qwen3*' --info | jq .`

Sure. What about a different protocol, say ollama?

`uvx llcat -u localhost:11434 -m '*qwen3*' --info | jq .`

All the abstraction without those pesky leaks.

----

**llcat** can:

 * Use local or remote servers, authenticated or not.
 * Store **conversation history** optionally, as a JSON file. 
 * Pipe things from stdin and/or be prompted on the command line.
 * Do **tool calling** using the OpenAI spec and MCP STDIO servers.
 * List and choose models, system prompts, and add attachments.
 * Schemas, dry-runs, expressing the calls as raw curls, adding body parameters (such as top_p or temperature), custom timeouts, customizing thinking or streaming, model info... and much more.

llcat's basic CLI parameters are also compatible with [Simon Willison's llm](https://github.com/simonw/llm).

Since conversations are just JSON files this makes context engineering trivial. There's even an included tool for sanely manipulating the JSONs.

## Examples

Here's some examples of how to use **llcat** as a building block for many common use-cases:

 * [Transferrable Conversations](#example-transferrable-conversations)
 * [Stateful Interaction](#example-adding-state)
 * [Interactive Chat](#example-interactive-chat)
 * [Structured Output](#example-structured-output)
 * [Evals](#example-evals)
 * [Tool Calling](#example-tool-calling)

## Example: Transferrable Conversations

Because conversations, models and servers are decoupled, you can mix and match them at any time.

Here's one conversation, hopping across models and servers.

Start a chat with Deepseek:
```
$ llcat -u https://openrouter.ai/api \
        -m deepseek/deepseek-r1-0528:free \
        -c /tmp/convo.txt \
        -sk "$(cat openrouter.key)" \
        "What is the capital of France?"
```

Continue it with Qwen using [MAS format](https://day50.dev/mas.html) and using the `@` syntax for including the key by file:
```
$ llcat -u "https://openrouter.ai/api#m=qwen/qwen3-4b:free"
        -c /tmp/convo.txt \
        -sk @openrouter.key \
        "And what about Canada?"
```

And finish on the local network:
```
$ llcat -u http://192.168.1.21:8080 \
        -c /tmp/convo.txt \
        "And what about Japan?"
```

Since the conversation goes to the filesystem as JSON you can use things like `inotify` or `fuse` and push it off to a vector search backend or modify the context window between calls.
 
## Example: Adding State

**llcat's** explicit syntax means lots of things are within reach.

For instance wrappers can be made custom to your workflow. 

Here's a way [to store state](https://github.com/day50-dev/llcat/blob/main/examples/state.sh) with environment variables to make invocation more convenient:

```shell
llf()        { llc "$@" 2> >(jq . >&2) | examples/spinner sd }
llc()        { llcat -m "$LLC_MODEL" -u "$LLC_SERVER" -sk "$LLC_KEY" "$@" }
llc-model()  { LLC_MODEL=$(llcat -m  -u "$LLC_SERVER" -sk "$LLC_KEY" | fzf) }
llc-server() { LLC_SERVER=$1 }
llc-key()    { LLC_KEY=$1 }
```

And now you can do things like this:
```shell
$ llc-server http://192.168.1.21:8080
$ llc "write a diss track where the knapsack problem hates on the towers of hanoi"
```

And what's that `llf` at the top? That uses `jq` to pretty print the errors and `streamdown` to pretty print the output along with a program to display a spinner while you wait.

There's no configuration files to parse or implicit states to manage.

## Example: Interactive Chat

A conversation interface is [also quick](https://github.com/day50-dev/llcat/blob/main/examples/conversation.sh):

```shell
#!/usr/bin/env bash

# We pick a file for the conversation or allow a user to pass it in with a CONV environment variable
conv=${CONV:-$(mktemp)}
echo -e "  Using: $conv\n"

# Show the previous conversation if there is any, stylize it with streamdown
jq -r '.[] | "\n**\(.role)**: \(.content)"' $conv | sd

# Read prompts in a loop
while read -E -p "  >> " query; do

    # Take the command line arguments of the shell script, pass them to llcat
    llcat -c $conv "$@" "$query" |& sd
    echo
done
```
So now instead of

`llcat -u http://myserver -k mykey -m model`

Our conversation loop can be invoked like

`conversation.sh -u http://myserver -k mykey -m model`

Adding additional features is trivial.

## Example: Structured Output

Using the schema feature you can pass json in to enforce a schema. Try something like

```shell
$ llcat -u http://localhost:11434 -sc @examples/schema.json "give me a person"
```


## Example: Evals

Running the same thing on multiple models and assessing the outcome is straight forward. Here we're using [ollama](https://ollama.com)

```shell
pre="llcat -u http://localhost:11434"
for model in $($pre -m); do
   $pre -m $model "translate 国際化がサポートされています。to english" > ${model}.outcome
done
```

You can use patterns like that also for testing tool calling completion. [Here's a bigger example: a humor eval to see if models know a funny joke when they see one](https://github.com/kristopolous/humor-evals)

If an error happens contacting the server, you get the request, response, and a non-zero exit.

Try this to see what that looks like

`uvx llcat -u fakecomputer`

## Example: Tool calling
The examples directory contains this [music playing tool](https://github.com/day50-dev/llcat/blob/main/examples/tool_program.py) listing the contents of [this album](https://elektrobopacek.bandcamp.com/album/untitled): 

```shell
$ llcat -u http://127.1:8080 -tf tool_file.json -tp tool_program.py "what mp3s do i have in my ~/mp3 directory"
{"level": "debug", "class": "toolcall", "message": "request", "obj": {"id": "iwCGjcRic8GAFB2jUvBUOeF9NNrldfxz", "type": "function", "function": {"name": "list_mp3s", "arguments": {"path":"~/mp3"}}}}
{"level": "debug", "class": "toolcall", "message": "result", "obj": ["Elektrobopacek - Towards the final Battle.mp3", "Elektrobopacek - Escape the Labyrinth.mp3", "Elektrobopacek - Journey to the misty Lands.mp3", "Elektrobopacek - Mistral Forte.mp3", "Elektrobopacek - Leaving Spaceport X-19.mp3", "Elektrobopacek - Dracula Rising.mp3"]}
Here are the MP3 files in your `~/mp3` directory:

1. **Elektrobopacek - Towards the final Battle.mp3**
2. **Elektrobopacek - Escape the Labyrinth.mp3**
3. **Elektrobopacek - Journey to the misty Lands.mp3**
4. **Elektrobopacek - Mistral Forte.mp3**
5. **Elektrobopacek - Leaving Spaceport X-19.mp3**
6. **Elektrobopacek - Dracula Rising.mp3**

Would you like to play any of these? Just share the filename, and I can play it for you! 🎵
```

In this example you can see how nothing is hidden so if the model makes a mistake it is immediately identifiable. 

The debug JSON objects are sent to `stderr` so routing it separately is trivial.

## MCP

### MCPFile
This file is what you usually need to make for an mcp server definition:

```json
{
  "mcpServers": {
    "<some_server>": {
      "command": "<some_command>",
      "args": ["<some>", "<args>"]
    }
    ...
  }
}
```

There's a basic extension on MCP here. You can explicity disable an MCP server by adding a flag `"disabled": true` like so:

```json
{
  "mcpServers": {
    "<some_server>": {
      "command": "<some_command>",
      "disabled": true,
      "args": ["<some>", "<args>"]
    }
    ...
  }
}
```

### MCPCat
MCP can be simple with simple tools. There's one included here. `mcpcat` is a 22 line Bash script. 

Here is an example of it in use:

```shell
$ mcpcat init list | \
  uv run python -m my-server | \
  jq .
```

Let's say there's a calculator mcp, you can do something like

```shell
$ mcpcat init call calculate '{"expression":"2+2"}' | \
   uv run python -m mcp_server_calculator \
   jq .
```

The beauty here is you can see the Emperor's new clothes up close. Simply omit the pipe.

```shell
$ mcpcat init call calculate '{"expression":"2+2"}'
{"jsonrpc":"2.0","id":4,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"mcpcat","version":"1.0"}}}
{"jsonrpc":"2.0","method":"notifications/initialized"}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"calculate","arguments":{"expression":"2+2"}}}
```

That's all the STDIO Transport is. 

There's ways of doing the network transports with this script as well. All you need is the appropriate network tools and compose away.

## Usage

Now it's your turn. 

```shell
usage: llcat [-h] [-su SERVER_URL] [-sk [@]SERVERKEY] [-to TIMEOUT]
             [-pr PROTO] [-m [MODEL]] [-s [@]SYSTEM] [-a ATTACH]
             [-c CONVERSATION] [-cr CONVERSATIONRO] [-eb [@]EXTRABODY]
             [-sc [@]SCHEMA] [-mf MCP_FILE] [-tp TOOL_PROGRAM]
             [-tf TOOL_FILE] [-ps] [-bq BE_QUIET] [-nt] [-ns] [-nw]
             [--curlify] [--dry] [--version] [--info [INFO]]
             [user_prompt ...]

llcat is /usr/bin/cat for LLMs. 

        🐱 Me-wow! 

https://github.com/day50-dev/llcat

Options with a [@] prefix can either be strings or paths to a file, curl style, @/like/this.

positional arguments:
  user_prompt           your prompt

options:
  -h, --help            show this help message and exit
  -su, -u, --server_url [@]SERVER_URL
                        server URL (e.g., http://::1:8080). Also supports MAS
                        format
  -sk, -k, --server_key [@]SERVERKEY
                        server API key for authorization
  -to, --timeout TIMEOUT
                        timeout in seconds for the read
  -pr, --proto PROTO    protocol to use (ollama, llama.cpp, openai, auto)
  -m, --model [@][MODEL]   model to use (or list models if no value)
  -s, --system [@]SYSTEM
                        system prompt
  -a, --attach ATTACH   attach file(s)
  -c, --conversation CONVERSATION
                        conversation history file (r/w)
  -cr, --conversationro CONVERSATIONRO
                        the readonly conversation input (ro)
  -eb, --extra_body [@]EXTRABODY
                        JSON to add to the body, such as max_tokens or
                        temperature
  -sc, --schema [@]SCHEMA
                        set a schema to force structured output
  -mf, --mcp_file MCP_FILE
                        MCP file to use
  -tp, --tool_program TOOL_PROGRAM
                        program to execute tool calls
  -tf, --tool_file TOOL_FILE
                        JSON file with tool definitions
  -ps, --ps             currently running model (if supported)
  -bq, --be_quiet BE_QUIET
                        make it shutup about things
  -nt, --no_think       disable thinking
  -ns, --no_stream      disable streaming
  -nw, --no_wrap        do not wrap inputs in <xml-like-syntax>
  --curlify             write curl equivalents of calls to stdout
  --dry                 dry run
  --version             show program's version number and exit
  --info [INFO]         get the info for a model
```

We're excited to see what you build.

Brought to you by **DA`/50**: Make the future obvious.
