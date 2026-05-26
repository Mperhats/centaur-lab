---
title: Image | Agent Sandbox
description: Create a Sandbox with custom dependencies.
url: https://agent-sandbox.sigs.k8s.io/docs/sandbox/custom_sandbox/image/
site: Agent Sandbox
generator: Hugo 0.150.0
---

### Agent Sandbox

# Image

Create a Sandbox with custom dependencies.

## Prerequisites

* A running Kubernetes cluster with the [Agent Sandbox Controller](/docs/getting_started/overview/) installed.
* The [Sandbox Router](https://github.com/kubernetes-sigs/agent-sandbox/blob/main/clients/python/agentic-sandbox-client/sandbox-router/README.md) deployed in your cluster.
* A `SandboxTemplate` named `python-sandbox-template` applied to your cluster. See the [Python Runtime Sandbox](/docs/runtime-templates/python/) guide for setup instructions.
* The [Python SDK](/docs/python-client/) installed: `pip install k8s-agent-sandbox`.

## Architecture

The Agent Sandbox architecture separates the **Template** (the definition) from the **Claim** (your Python request) for a very specific reason: **speed**. When a user applies a `SandboxTemplate` to a Kubernetes cluster, the controller typically spins up a `SandboxWarmPool`. These are pre-initialized, running pods that have already pulled your specific Docker image. When a `Python` script calls `client.create_sandbox("sandbox-template")`, it instantly grabs one of these pre-warmed pods.

## Workarounds

### 1. Install dependencies via sandbox.commands.run() function

```python
from k8s_agent_sandbox import SandboxClient
client = SandboxClient()
sandbox = client.create_sandbox("python-sandbox-template")
# Dynamically install a package before running your main logic
sandbox.commands.run("pip install custom-package==1.0.0")
response = sandbox.commands.run("python -c 'import custom_package; print(\"Success!\")'")
```

Last modified April 30, 2026: [reorder pages, remove duplicate (#717) (8e62071)](https://github.com/kubernetes-sigs/agent-sandbox/commit/8e62071407f9c747ea8af7372319325e978755b1)

---

Powered by [curl.md](https://curl.md)
