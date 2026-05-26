---
title: Documentation | Agent Sandbox
description: |-
  What is Agent Sandbox?

      
      
      



  Agent Sandbox is a Kubernetes-native platform for managing isolated, stateful, singleton workloads — …
url: https://agent-sandbox.sigs.k8s.io/docs/
site: Agent Sandbox
generator: Hugo 0.150.0
---

### Agent Sandbox

# Documentation

## What is Agent Sandbox?

Agent Sandbox is a Kubernetes-native platform for managing isolated, stateful, singleton workloads — purpose-built for AI agent runtimes, development environments, and any scenario that demands a long-running container with a stable identity.

At its core, Agent Sandbox introduces the `Sandbox` Custom Resource Definition (CRD) and a set of extension CRDs (`SandboxTemplate`, `SandboxClaim`, `SandboxWarmPool`) that together give you a declarative, standardized API on top of Kubernetes primitives. Instead of stitching together StatefulSets, Services, and PersistentVolumeClaims by hand, you describe the sandbox you want and let the controller handle the rest.

## Why Agent Sandbox?

### Fast sandbox provisioning

The `SandboxWarmPool` extension pre-warms a pool of pods so that when a new sandbox is needed — for a code execution request or a fresh agent session — it can be assigned in milliseconds rather than waiting for a cold pod to schedule and start. This dramatically reduces latency for interactive workloads and high-throughput agent pipelines.

### Strong, configurable isolation

Agent Sandbox is runtime-agnostic. You can pair it with [gVisor](https://gvisor.dev/) for kernel-level sandboxing or [Kata Containers](https://katacontainers.io/) for VM-grade isolation, making it suitable for executing untrusted or LLM-generated code in multi-tenant clusters. Isolation depth is a deployment choice, not a limitation of the API.

### Stable identity and persistent storage

Each Sandbox has a stable hostname and can be backed by persistent storage that survives restarts. Agents and tools can reconnect to the same environment across sessions, preserving installed packages, files, and in-progress work without any application-level coordination.

### Lifecycle management built in

The Sandbox controller handles the full lifecycle out of the box: creation, scheduled deletion, pausing (hibernation), and automatic resume on incoming network connections. Hibernation saves compute costs during idle periods while keeping state intact for seamless resumption.

### Kubernetes-native and extensible

Agent Sandbox builds on standard Kubernetes primitives and integrates cleanly with existing cluster tooling — RBAC, namespaces, network policies, and resource quotas all apply as usual. The extension CRDs let platform teams define reusable `SandboxTemplate`s so developers can claim sandboxes without needing to know the underlying configuration details.

### Client SDKs for programmatic access

Agent Sandbox provides first-class clients for both [Python](/docs/python-client/) and [Go](/docs/go-client/), so agents and applications can create, query, and manage sandboxes programmatically in the language that best fits their runtime and platform.

## Core capabilities

| Capability | Description |
| --- | --- |
| **Sandbox CRD** | Declarative API for a single, stateful pod with a stable hostname and optional persistent storage |
| **SandboxTemplate** | Reusable templates that codify runtime configuration for consistent sandbox provisioning |
| **SandboxClaim** | User-facing abstraction that provisions a sandbox from a template without exposing low-level details |
| **SandboxWarmPool** | Pre-warmed pod pools for near-instant sandbox allocation |
| **Hibernation & resume** | Pause sandboxes to free compute resources; resume automatically on network activity |
| **Runtime flexibility** | Works with standard containers, gVisor, Kata Containers, and other OCI-compatible runtimes |
| **Python SDK** | High-level client library for programmatic sandbox management in Python-based agent runtimes |
| **Go SDK** | High-level client library for programmatic sandbox management in Go services and controllers |
| **Scheduled deletion** | Automatic cleanup of sandboxes after a configurable TTL |

## Where to go next

[Getting Started](/docs/getting_started/)

[This page provides a set of guides to help you get started with the Agent Sandbox.](/docs/getting_started/)

[Use Cases](/docs/use-cases/)

[Explore common use cases for Agent Sandbox — from short-lived code execution to always-on agent environments.](/docs/use-cases/)

[Sandbox](/docs/sandbox/)

[Filesystem](/docs/filesystem/)

[Read, write, list, and transfer files inside sandboxes using the Python SDK.](/docs/filesystem/)

[Volume with Sandbox](/docs/volumes/)

[This page provides documentation on how to configure Volumes for an Agent Sandbox.](/docs/volumes/)

[Go Client](/docs/go-client/)

[This section describes how to use the Go Client](/docs/go-client/)

[Python Client](/docs/python-client/)

[This section describes how to use the Python Client](/docs/python-client/)

[Runtime Templates](/docs/runtime-templates/)

[This page provides a collection of runtime templates and code examples for integrating the agent sandbox into your projects.](/docs/runtime-templates/)

[Contribution Guidelines](/docs/contribution-guidelines/)

[How to contribute to Agent Sandbox](/docs/contribution-guidelines/)

[API Documentation](/docs/api/)

[Technical reference for the Agent Sandbox API resources and types](/docs/api/)

Last modified April 24, 2026: [docs: add Go sandbox client documentation to docsite (#675) (effb356)](https://github.com/kubernetes-sigs/agent-sandbox/commit/effb35652a946dda27de1c962b869d12c2787ec4)

---

Powered by [curl.md](https://curl.md)
