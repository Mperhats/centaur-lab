---
title: Running and debugging tests | Agent Sandbox
description: Running and debugging tests
url: https://agent-sandbox.sigs.k8s.io/docs/contribution-guidelines/testing/
site: Agent Sandbox
generator: Hugo 0.150.0
---

### Agent Sandbox

# Running and debugging tests

Running and debugging tests

```shell
make deploy-kind
```

## Running unit tests

To run all unit tests:

```shell
make test-unit
```

## Running the e2e tests (including benchmarks)

To run all e2e tests:

```shell
make test-e2e
```

## Running only e2e benchmarks

To run only e2e benchmarks:

```shell
make test-e2e-benchmarks
```

## Running Tests with Race Detector

Go unit tests run with Go’s race detector (`-race`) enabled. E2e tests do not run with -race by default, since the race detector significantly increases memory usage (5-10×) and execution time (2-20×), which would slow down PR presubmits.

To run e2e tests with race detection:

```shell
make test-e2e-race
```

## Remove the kind cluster

```shell
make delete-kind
```

### See also

* [Kubernetes testing guide](https://github.com/kubernetes/community/blob/master/contributors/devel/sig-testing/testing.md)
* [Integration Testing in Kubernetes](https://github.com/kubernetes/community/blob/master/contributors/devel/sig-testing/integration-tests.md)
* [End-to-End Testing in Kubernetes](https://github.com/kubernetes/community/blob/master/contributors/devel/sig-testing/e2e-tests.md)
* [Flaky Tests in Kubernetes](https://github.com/kubernetes/community/blob/master/contributors/devel/sig-testing/flaky-tests.md)

Last modified April 2, 2026: [Website reorg + fixing all the links (#257) (c3c9f03)](https://github.com/kubernetes-sigs/agent-sandbox/commit/c3c9f0347480d891d6c853850ea748056474a8e5)

---

Powered by [curl.md](https://curl.md)
