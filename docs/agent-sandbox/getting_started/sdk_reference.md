---
title: SDK Reference | Agent Sandbox
description: Agent Sandbox Python SDK Reference.
url: https://agent-sandbox.sigs.k8s.io/docs/getting_started/sdk_reference/
site: Agent Sandbox
generator: Hugo 0.150.0
---

### Agent Sandbox

# SDK Reference

Agent Sandbox Python SDK Reference.

## k8s\_agent\_sandbox.sandbox\_client

This module provides the SandboxClient for interacting with the Agentic Sandbox. It handles lifecycle management (claiming, waiting) and interaction (execution, file I/O) via the Sandbox resource handle.

### SandboxClient Objects

```python
class SandboxClient(Generic[T])
```

A registry-based client for managing Sandbox lifecycles. Tracks all active handles to ensure flat code structure and safe cleanup.

##### sandbox\_class

type: ignore

##### \_\_init\_\_

```python
def __init__(connection_config: SandboxConnectionConfig | None = None,
             tracer_config: SandboxTracerConfig | None = None,
             cleanup: bool = False)
```

Initializes the SandboxClient.

**Arguments**:

* `connection_config` - Configuration for connecting to the sandboxes. Defaults to SandboxLocalTunnelConnectionConfig() which uses kubectl port-forwarding. Can also be SandboxDirectConnectionConfig or SandboxGatewayConnectionConfig.
* `tracer_config` - Configuration for OpenTelemetry tracing. Defaults to an empty SandboxTracerConfig (tracing disabled).
* `cleanup` - If True, registers an atexit hook to automatically delete all tracked sandboxes when the program terminates. Defaults to False.

##### create\_sandbox

```python
def create_sandbox(template: str,
                   namespace: str = "default",
                   sandbox_ready_timeout: int = 180,
                   labels: dict[str, str] | None = None,
                   *,
                   shutdown_after_seconds: int | None = None) -> T
```

Provisions new Sandbox claim and returns a Sandbox handle which tracks the underlying infrastructure.

**Arguments**:

* `template` - Name of the SandboxTemplate to use.
* `namespace` - Kubernetes namespace for the claim.
* `sandbox_ready_timeout` - Seconds to wait for the sandbox to be ready.
* `labels` - Optional Kubernetes labels to attach to the claim.
* `shutdown_after_seconds` - Optional TTL in seconds. When set, the claim’s `spec.lifecycle` is populated with a `shutdownTime` of *now + shutdown\_after\_seconds* (UTC) and a `shutdownPolicy` of `"Delete"`, so the controller auto-deletes the claim on expiry. Must be a positive integer.

**Example**:

> > > client = SandboxClient() sandbox = client.create\_sandbox(template=“python-sandbox-template”) sandbox.commands.run(“echo ‘Hello World’”)

##### get\_sandbox

```python
def get_sandbox(claim_name: str,
                namespace: str = "default",
                resolve_timeout: int = 30) -> T
```

Retrieves an existing sandbox handle given a sandbox claim name. If the handle is closed or missing, it re-attaches to the infrastructure.

**Example**:

> > > client = SandboxClient() sandbox = client.get\_sandbox(“sandbox-claim-1234abcd”) sandbox.commands.run(“ls -la”)

##### list\_active\_sandboxes

```python
def list_active_sandboxes() -> List[Tuple[str, str]]
```

Returns a list of tuples containing (namespace, claim\_name) currently managed by this client.

**Example**:

> > > client = SandboxClient() client.create\_sandbox(“python-sandbox-template”) print(client.list\_active\_sandboxes()) \[(‘default’, ‘sandbox-claim-1234abcd’)]

##### list\_all\_sandboxes

```python
def list_all_sandboxes(namespace: str = "default") -> List[str]
```

Lists all SandboxClaim names currently existing in the Kubernetes cluster for the given namespace.

**Example**:

> > > client = SandboxClient() print(client.list\_all\_sandboxes(namespace=“default”)) \[‘sandbox-claim-1234abcd’, ‘sandbox-claim-5678efgh’]

##### delete\_sandbox

```python
def delete_sandbox(claim_name: str, namespace: str = "default")
```

Stops the client side connection and deletes the Kubernetes resources.

**Example**:

> > > client = SandboxClient() sandbox = client.create\_sandbox(“python-sandbox-template”) client.delete\_sandbox(sandbox.claim\_name)

##### delete\_all

```python
def delete_all()
```

Cleanup all tracked sandboxes managed by this client.

**Example**:

> > > client = SandboxClient() client.create\_sandbox(“python-sandbox-template”) client.create\_sandbox(“python-sandbox-template”) client.delete\_all()

## k8s\_agent\_sandbox.models

### ExecutionResult Objects

```python
class ExecutionResult(BaseModel)
```

A structured object for holding the result of a command execution.

##### stdout

Standard output from the command.

##### stderr

Standard error from the command.

##### exit\_code

Exit code of the command.

### FileEntry Objects

```python
class FileEntry(BaseModel)
```

Represents a file or directory entry in the sandbox.

##### name

Name of the file.

##### size

Size of the file in bytes.

##### type

Type of the entry (file or directory).

##### mod\_time

Last modification time of the file. (POSIX timestamp)

### SandboxDirectConnectionConfig Objects

```python
class SandboxDirectConnectionConfig(BaseModel)
```

Configuration for connecting directly to a Sandbox URL.

##### api\_url

Direct URL to the router.

##### server\_port

Port the sandbox container listens on.

### SandboxGatewayConnectionConfig Objects

```python
class SandboxGatewayConnectionConfig(BaseModel)
```

Configuration for connecting via Kubernetes Gateway API.

##### gateway\_name

Name of the Gateway resource.

##### gateway\_namespace

Namespace where the Gateway resource resides.

##### gateway\_ready\_timeout

Timeout in seconds to wait for Gateway IP.

##### server\_port

Port the sandbox container listens on.

### SandboxLocalTunnelConnectionConfig Objects

```python
class SandboxLocalTunnelConnectionConfig(BaseModel)
```

Configuration for connecting via kubectl port-forward.

##### port\_forward\_ready\_timeout

Timeout in seconds to wait for port-forward to be ready.

##### server\_port

Port the sandbox container listens on.

### SandboxTracerConfig Objects

```python
class SandboxTracerConfig(BaseModel)
```

Configuration for tracer level information

##### enable\_tracing

Whether to enable OpenTelemetry tracing.

##### trace\_service\_name

Service name used for traces.

Last modified April 24, 2026: [Docs agent sandbox references (#651) (2d964f6)](https://github.com/kubernetes-sigs/agent-sandbox/commit/2d964f6081fe4dd29d45ed03710abb222dcc802a)

---

Powered by [curl.md](https://curl.md)
