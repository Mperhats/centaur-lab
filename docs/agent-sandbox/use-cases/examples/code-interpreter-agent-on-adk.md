---
title: Using Agent Sandbox as a Tool in Agent Development Kit (ADK) | Agent Sandbox
description: This guide walks you through the process of creating a simple ADK agent that uses Agent Sandbox as a tool to execute Python code in a secure sandboxed environment.
url: https://agent-sandbox.sigs.k8s.io/docs/use-cases/examples/code-interpreter-agent-on-adk/
site: Agent Sandbox
generator: Hugo 0.150.0
---

### Agent Sandbox

# Using Agent Sandbox as a Tool in Agent Development Kit (ADK)

This guide walks you through the process of creating a simple ADK agent that uses Agent Sandbox as a tool to execute Python code in a secure sandboxed environment.

The guide walks you through the process of creating a simple [ADK](https://google.github.io/adk-docs/) agent that is able to use agent sandbox as a tool.

## Installation

1. Install the Agent-Sandbox controller and CRDs to a cluster. You can follow the instructions from the [installation section from the Getting Started page](/docs/getting_started/overview/#installation).

2. Install the Agent Sandbox [router](https://github.com/kubernetes-sigs/agent-sandbox/blob/main/clients/python/agentic-sandbox-client/README.md#setup-deploying-the-router)

3. Create a Python virtual environment:

   ```sh
   python3 -m venv .venv
   source .venv/bin/activate
   ```

4. Install the dependencies:

   ```sh
   export VERSION="main"
   pip install google-adk==1.19.0 "git+https://github.com/kubernetes-sigs/agent-sandbox.git@${VERSION}#subdirectory=clients/python/agentic-sandbox-client"
   ```

5. Create a new ADK project:

   ```sh
   adk create coding_agent
   ```

6. Replace the content of the `coding_agent/agent.py` file with the following:

   ```sh
   from google.adk.agents.llm_agent import Agent
   from k8s_agent_sandbox import SandboxClient


   def execute_python(code: str):
       sb = SandboxClient()
       sandbox = sb.create_sandbox(template="python-sandbox-template", namespace="default")
       try:
        sandbox.files.write("run.py", code)
        result = sandbox.commands.run("python3 run.py")
        return result.stdout
       finally:
         sandbox.terminate()


   root_agent = Agent(
       model='gemini-2.5-flash',
       name='coding_agent',
       description="Writes Python code and executes it in a sandbox.",
       instruction="You are a helpful assistant that can write Python code and execute it in the sandbox. Use the 'execute_python' tool for this purpose.",
       tools=[execute_python],
   )
   ```

   As you can see, the Agent Sandbox is called by a wrapper function `execute_python` which, in turn, is used by the `Agent` class as a tool.

7. Run the agent in ADK’s built in server:

   ```sh
   adk web
   ```

## Testing

1. Open the agent’s page: http\://127.0.0.1:8000.

2. Tell the agent to generate some code and execute it in the sandbox:

![example](https://github.com/kubernetes-sigs/agent-sandbox/blob/main/example.png)

The agent should generate the code and execute it in the agent-sandbox.

Last modified April 23, 2026: [Docs feature use cases (#652) (0840ee5)](https://github.com/kubernetes-sigs/agent-sandbox/commit/0840ee5040a4a8433aad6d2ed46956cb7dda3bc6)

---

Powered by [curl.md](https://curl.md)
