## data

---
title: API Documentation | Agent Sandbox
description: Technical reference for the Agent Sandbox API resources and types
url: https://agent-sandbox.sigs.k8s.io/docs/api/
site: Agent Sandbox
generator: Hugo 0.150.0
---

### Agent Sandbox

# API Documentation

Technical reference for the Agent Sandbox API resources and types

## Packages

* agents.x-k8s.io/v1alpha1
* extensions.agents.x-k8s.io/v1alpha1

## agents.x-k8s.io/v1alpha1

Package v1alpha1 contains API Schema definitions for the agents v1alpha1 API group

Package v1alpha1 contains API Schema definitions for the agents v1alpha1 API group.

### Resource Types

* Sandbox

#### EmbeddedObjectMetadata

*Appears in:*

* PersistentVolumeClaimTemplate

| Field | Description | Default | Validation |
| --- | --- | --- | --- |
| `name` *string* | name must be unique within a namespace. Is required when creating resources, although some resources may allow a client to request the generation of an appropriate name automatically. Name is primarily intended for creation idempotence and configuration definition. Cannot be updated. More info: <https://kubernetes.io/docs/concepts/overview/working-with-objects/names#names> | | Optional: {} |
| `labels` *object (keys:string, values:string)* | labels defines the map of string keys and values that can be used to organize and categorize (scope and select) objects. May match selectors of replication controllers and services. More info: <https://kubernetes.io/docs/concepts/overview/working-with-objects/labels> | | Optional: {} |
| `annotations` *object (keys:string, values:string)* | annotations is an unstructured key value map stored with a resource that may be set by external tools to store and retrieve arbitrary metadata. They are not queryable and should be preserved when modifying objects. More info: <https://kubernetes.io/docs/concepts/overview/working-with-objects/annotations> | | Optional: {} |

#### Lifecycle

Lifecycle defines the lifecycle management for the Sandbox.

*Appears in:*

* SandboxSpec

| Field | Description | Default | Validation |
| --- | --- | --- | --- |
| `shutdownTime` *[Time](https://kubernetes.io/docs/reference/generated/kubernetes-api/v/#time-v1-meta)* | shutdownTime is the absolute time when the sandbox expires. | | Format: date-time Optional: {} |
| `shutdownPolicy` *ShutdownPolicy* | shutdownPolicy determines if the Sandbox resource itself should be deleted when it expires. Underlying resources(Pods, Services) are always deleted on expiry. | Retain | Enum: \[Delete Retain] Optional: {} |

#### PersistentVolumeClaimTemplate

*Appears in:*

* SandboxSpec
* SandboxTemplateSpec

| Field | Description | Default | Validation |
| --- | --- | --- | --- |
| `metadata` *EmbeddedObjectMetadata* | Refer to Kubernetes API documentation for fields of `metadata`. | | Optional: {} |
| `spec` *[PersistentVolumeClaimSpec](https://kubernetes.io/docs/reference/generated/kubernetes-api/v/#persistentvolumeclaimspec-v1-core)* | spec is the PVC’s spec | | Required: {} |

#### PodMetadata

*Appears in:*

* PodTemplate
* SandboxClaimSpec

| Field | Description | Default | Validation |
| --- | --- | --- | --- |
| `labels` *object (keys:string, values:string)* | labels defines the map of string keys and values that can be used to organize and categorize (scope and select) objects. May match selectors of replication controllers and services. More info: <https://kubernetes.io/docs/concepts/overview/working-with-objects/labels> | | Optional: {} |
| `annotations` *object (keys:string, values:string)* | annotations is an unstructured key value map stored with a resource that may be set by external tools to store and retrieve arbitrary metadata. They are not queryable and should be preserved when modifying objects. More info: <https://kubernetes.io/docs/concepts/overview/working-with-objects/annotations> | | Optional: {} |

#### PodTemplate

*Appears in:*

* SandboxSpec
* SandboxTemplateSpec

| Field | Description | Default | Validation |
| --- | --- | --- | --- |
| `spec` *[PodSpec](https://kubernetes.io/docs/reference/generated/kubernetes-api/v/#podspec-v1-core)* | spec is the Pod’s spec | | Required: {} |
| `metadata` *PodMetadata* | Refer to Kubernetes API documentation for fields of `metadata`. | | Optional: {} |

#### Sandbox

Sandbox is the Schema for the sandboxes API.

| Field | Description | Default | Validation |
| --- | --- | --- | --- |
| `apiVersion` *string* | `agents.x-k8s.io/v1alpha1` | | |
| `kind` *string* | `Sandbox` | | |
| `kind` *string* | Kind is a string value representing the REST resource this object represents. Servers may infer this from the endpoint the client submits requests to. Cannot be updated. In CamelCase. More info: <https://git.k8s.io/community/contributors/devel/sig-architecture/api-conventions.md#types-kinds> | | Optional: {} |
| `apiVersion` *string* | APIVersion defines the versioned schema of this representation of an object. Servers should convert recognized schemas to the latest internal value, and may reject unrecognized values. More info: <https://git.k8s.io/community/contributors/devel/sig-architecture/api-conventions.md#resources> | | Optional: {} |
| `metadata` *[ObjectMeta](https://kubernetes.io/docs/reference/generated/kubernetes-api/v/#objectmeta-v1-meta)* | Refer to Kubernetes API documentation for fields of `metadata`. | | Optional: {} |
| `spec` *SandboxSpec* | spec defines the desired state of Sandbox | | Required: {} |
| `status` *SandboxStatus* | status defines the observed state of Sandbox | | Optional: {} |

#### SandboxSpec

SandboxSpec defines the desired state of Sandbox.

*Appears in:*

* Sandbox

| Field | Description | Default | Validation |
| --- | --- | --- | --- |
| `podTemplate` *PodTemplate* | podTemplate describes the pod spec that will be used to create an agent sandbox. | | Required: {} |
| `volumeClaimTemplates` *PersistentVolumeClaimTemplate array* | volumeClaimTemplates is a list of claims that the sandbox pod is allowed to reference. Every claim in this list must have at least one matching access mode with a provisioner volume. | | Optional: {} |
| `shutdownTime` *[Time](https://kubernetes.io/docs/reference/generated/kubernetes-api/v/#time-v1-meta)* | shutdownTime is the absolute time when the sandbox expires. | | Format: date-time Optional: {} |
| `shutdownPolicy` *ShutdownPolicy* | shutdownPolicy determines if the Sandbox resource itself should be deleted when it expires. Underlying resources(Pods, Services) are always deleted on expiry. | Retain | Enum: \[Delete Retain] Optional: {} |
| `replicas` *integer* | replicas is the number of desired replicas. The only allowed values are 0 and 1. Defaults to 1. | 1 | Maximum: 1 Minimum: 0 Optional: {} |

#### SandboxStatus

SandboxStatus defines the observed state of Sandbox.

*Appears in:*

* Sandbox

| Field | Description | Default | Validation |
| --- | --- | --- | --- |
| `serviceFQDN` *string* | serviceFQDN that is valid for default cluster settings The domain defaults to cluster.local but is configurable via the controller’s –cluster-domain flag. | | Optional: {} |
| `service` *string* | service is a sandbox-example | | Optional: {} |
| `conditions` *[Condition](https://kubernetes.io/docs/reference/generated/kubernetes-api/v/#condition-v1-meta) array* | conditions defines the status conditions array | | Optional: {} |
| `replicas` *integer* | replicas is the number of actual replicas. | | Minimum: 0 Optional: {} |
| `selector` *string* | selector is the label selector for pods. | | Optional: {} |
| `podIPs` *string array* | podIPs are the IP addresses of the underlying pod. A pod may have multiple IPs in dual-stack clusters. | | Optional: {} |

#### ShutdownPolicy

*Underlying type:* *string*

ShutdownPolicy describes the policy for deleting the Sandbox when it expires.

*Validation:*

* Enum: \[Delete Retain]

*Appears in:*

* Lifecycle
* SandboxSpec

| Field | Description |
| --- | --- |
| `Delete` | ShutdownPolicyDelete deletes the Sandbox when expired. |
| `Retain` | ShutdownPolicyRetain keeps the Sandbox when expired (Status will show Expired). |

## extensions.agents.x-k8s.io/v1alpha1

Package v1alpha1 contains API Schema definitions for the extensions v1alpha1 API group

Package v1alpha1 contains API Schema definitions for the agents v1alpha1 API group.

### Resource Types

* SandboxClaim
* SandboxTemplate
* SandboxWarmPool

#### EnvVar

EnvVar represents a custom environment variable key-value pair.

*Appears in:*

* SandboxClaimSpec

| Field | Description | Default | Validation |
| --- | --- | --- | --- |
| `name` *string* | name of the environment variable. | | Required: {} |
| `value` *string* | value of the environment variable. | | Required: {} |
| `containerName` *string* | containerName specifies the target container for the environment variable. If not specified, it defaults to the first container defined in the template. | | Optional: {} |

#### EnvVarsInjectionPolicy

*Underlying type:* *string*

EnvVarsInjectionPolicy defines whether a SandboxClaim is allowed to inject or override environment variables.

*Appears in:*

* SandboxTemplateSpec

| Field | Description |
| --- | --- |
| `Allowed` | EnvVarsInjectionPolicyAllowed allows a SandboxClaim to inject new environment variables, but not override existing ones. |
| `Overrides` | EnvVarsInjectionPolicyOverrides allows a SandboxClaim to inject new and override existing environment variables. |
| `Disallowed` | EnvVarsInjectionPolicyDisallowed prevents a SandboxClaim from injecting any environment variables. |

#### Lifecycle

Lifecycle defines the lifecycle management for the SandboxClaim.

*Appears in:*

* SandboxClaimSpec

| Field | Description | Default | Validation |
| --- | --- | --- | --- |
| `shutdownTime` *[Time](https://kubernetes.io/docs/reference/generated/kubernetes-api/v/#time-v1-meta)* | shutdownTime is the absolute time when the SandboxClaim expires. This time governs the lifecycle of the claim. It is not propagated to the underlying Sandbox. Instead, the SandboxClaim controller enforces this expiration by deleting the Sandbox resources when the time is reached. If this field is omitted or set to nil, the SandboxClaim itself won’t expire. This implies unsetting a Sandbox’s ShutdownTime via SandboxClaim isn’t supported. | | Format: date-time Optional: {} |
| `ttlSecondsAfterFinished` *integer* | ttlSecondsAfterFinished limits how long a finished claim is retained. The timer starts from the mirrored Finished condition’s LastTransitionTime. | | Minimum: 0 Optional: {} |
| `shutdownPolicy` *ShutdownPolicy* | shutdownPolicy determines the behavior when the SandboxClaim expires. | Retain | Enum: \[Delete DeleteForeground Retain] Optional: {} |

#### NetworkPolicyManagement

*Underlying type:* *string*

NetworkPolicyManagement defines whether the controller automatically generates and manages a shared NetworkPolicy for this template.

*Appears in:*

* SandboxTemplateSpec

| Field | Description |
| --- | --- |
| `Managed` | NetworkPolicyManagementManaged means the controller will ensure a shared NetworkPolicy exists. This shared NetworkPolicy will be a user provide one or a default controller created policy. This is the default behavior if the field is omitted. |
| `Unmanaged` | NetworkPolicyManagementUnmanaged means the controller will skip NetworkPolicy creation entirely, allowing external systems (like Cilium) to manage networking. |

#### NetworkPolicySpec

NetworkPolicySpec defines the desired state of the NetworkPolicy.

*Appears in:*

* SandboxTemplateSpec

| Field | Description | Default | Validation |
| --- | --- | --- | --- |
| `ingress` *[NetworkPolicyIngressRule](https://kubernetes.io/docs/reference/generated/kubernetes-api/v/#networkpolicyingressrule-v1-networking) array* | ingress is a list of ingress rules to be applied to the sandbox. Traffic is allowed to the sandbox if it matches at least one rule. If this list is empty, all ingress traffic is blocked (Default Deny). | | Optional: {} |
| `egress` *[NetworkPolicyEgressRule](https://kubernetes.io/docs/reference/generated/kubernetes-api/v/#networkpolicyegressrule-v1-networking) array* | egress is a list of egress rules to be applied to the sandbox. Traffic is allowed out of the sandbox if it matches at least one rule. If this list is empty, all egress traffic is blocked (Default Deny). | | Optional: {} |

#### SandboxClaim

SandboxClaim is the Schema for the sandbox Claim API.

| Field | Description | Default | Validation |
| --- | --- | --- | --- |
| `apiVersion` *string* | `extensions.agents.x-k8s.io/v1alpha1` | | |
| `kind` *string* | `SandboxClaim` | | |
| `kind` *string* | Kind is a string value representing the REST resource this object represents. Servers may infer this from the endpoint the client submits requests to. Cannot be updated. In CamelCase. More info: <https://git.k8s.io/community/contributors/devel/sig-architecture/api-conventions.md#types-kinds> | | Optional: {} |
| `apiVersion` *string* | APIVersion defines the versioned schema of this representation of an object. Servers should convert recognized schemas to the latest internal value, and may reject unrecognized values. More info: <https://git.k8s.io/community/contributors/devel/sig-architecture/api-conventions.md#resources> | | Optional: {} |
| `metadata` *[ObjectMeta](https://kubernetes.io/docs/reference/generated/kubernetes-api/v/#objectmeta-v1-meta)* | Refer to Kubernetes API documentation for fields of `metadata`. | | Optional: {} |
| `spec` *SandboxClaimSpec* | spec defines the desired state of Sandbox | | Required: {} |
| `status` *SandboxClaimStatus* | status defines the observed state of Sandbox | | Optional: {} |

#### SandboxClaimSpec

SandboxClaimSpec defines the desired state of Sandbox.

*Appears in:*

* SandboxClaim

| Field | Description | Default | Validation |
| --- | --- | --- | --- |
| `sandboxTemplateRef` *SandboxTemplateRef* | sandboxTemplateRef defines the name of the SandboxTemplate to be used for creating a Sandbox. | | Required: {} |
| `lifecycle` *Lifecycle* | lifecycle defines when and how the SandboxClaim should be shut down. | | Optional: {} |
| `warmpool` *WarmPoolPolicy* | warmpool specifies the warm pool policy for sandbox adoption. - “none”: Do not use any warm pool, always create fresh sandboxes - “default”: Use default behavior, select from all matching warm pools (default) - A warm pool name: Select only from the specified warm pool (e.g., “fast-pool”, “secure-pool”) | default | Optional: {} |
| `additionalPodMetadata` *PodMetadata* | additionalPodMetadata defines the labels and annotations to be propagated to the Sandbox Pod. Label values are limited to 63 characters and must match Kubernetes label value patterns. | | Optional: {} |
| `env` *EnvVar array* | env is a list of environment variables to inject into the sandbox | | Optional: {} |

#### SandboxClaimStatus

SandboxClaimStatus defines the observed state of Sandbox.

*Appears in:*

* SandboxClaim

| Field | Description | Default | Validation |
| --- | --- | --- | --- |
| `conditions` *[Condition](https://kubernetes.io/docs/reference/generated/kubernetes-api/v/#condition-v1-meta) array* | conditions represent the latest available observations of a Sandbox’s current state. | | Optional: {} |
| `sandbox` *SandboxStatus* | sandbox defines the state of Sandbox | | Optional: {} |

#### SandboxStatus

*Appears in:*

* SandboxClaimStatus

| Field | Description | Default | Validation |
| --- | --- | --- | --- |
| `name` *string* | name is the name of the Sandbox created from this claim | | Optional: {} |
| `podIPs` *string array* | podIPs are the IP addresses of the underlying pod. A pod may have multiple IPs in dual-stack clusters. | | Optional: {} |

#### SandboxTemplate

SandboxTemplate is the Schema for the sandbox template API.

| Field | Description | Default | Validation |
| --- | --- | --- | --- |
| `apiVersion` *string* | `extensions.agents.x-k8s.io/v1alpha1` | | |
| `kind` *string* | `SandboxTemplate` | | |
| `kind` *string* | Kind is a string value representing the REST resource this object represents. Servers may infer this from the endpoint the client submits requests to. Cannot be updated. In CamelCase. More info: <https://git.k8s.io/community/contributors/devel/sig-architecture/api-conventions.md#types-kinds> | | Optional: {} |
| `apiVersion` *string* | APIVersion defines the versioned schema of this representation of an object. Servers should convert recognized schemas to the latest internal value, and may reject unrecognized values. More info: <https://git.k8s.io/community/contributors/devel/sig-architecture/api-conventions.md#resources> | | Optional: {} |
| `metadata` *[ObjectMeta](https://kubernetes.io/docs/reference/generated/kubernetes-api/v/#objectmeta-v1-meta)* | Refer to Kubernetes API documentation for fields of `metadata`. | | Optional: {} |
| `spec` *SandboxTemplateSpec* | spec defines the desired state of Sandbox | | Required: {} |
| `status` *SandboxTemplateStatus* | status defines the observed state of Sandbox | | Optional: {} |

#### SandboxTemplateRef

SandboxTemplateRef references a SandboxTemplate.

*Appears in:*

* SandboxClaimSpec
* SandboxWarmPoolSpec

| Field | Description | Default | Validation |
| --- | --- | --- | --- |
| `name` *string* | name of the SandboxTemplate | | Required: {} |

#### SandboxTemplateSpec

SandboxTemplateSpec defines the desired state of Sandbox.

*Appears in:*

* SandboxTemplate

| Field | Description | Default | Validation |
| --- | --- | --- | --- |
| `podTemplate` *PodTemplate* | podTemplate defines the object template that describes the pod spec that will be used to create an agent sandbox. If AutomountServiceAccountToken is not specified in the PodSpec, it defaults to false to ensure a secure-by-default environment. | | Required: {} |
| `volumeClaimTemplates` *PersistentVolumeClaimTemplate array* | volumeClaimTemplates is a list of claims that pods created from this template are allowed to reference. When a SandboxClaim or SandboxWarmPool creates a sandbox from this template, PVCs will be created from these templates. Every claim in this list must have at least one matching access mode with a provisioner volume. NOTE: This list is atomic. Updates to this field will replace the entire list rather than merging with existing entries. | | Optional: {} |
| `networkPolicy` *NetworkPolicySpec* | networkPolicy defines the network policy to be applied to the sandboxes created from this template. A single shared NetworkPolicy is created per Template. Behavior is dictated by the NetworkPolicyManagement field: - If Management is “Unmanaged”: This field is completely ignored. - If Management is “Managed” (default) and this field is omitted (nil): The controller automatically applies a strict Secure Default policy: \* Ingress: Allow traffic only from the Sandbox Router. \* Egress: Allow Public Internet only. Blocks internal IPs (RFC1918), Metadata Server, etc. - If Management is “Managed” and this field is provided: The controller applies your custom rules. Update Behavior: Because the NetworkPolicy is shared at the template level, any updates to these rules will be applied to the single shared policy object. The underlying Kubernetes CNI will then dynamically enforce the updated rules across all existing and future sandboxes referencing this template. NOTE: This is a restricted subset of the standard Kubernetes NetworkPolicySpec. Fields like ‘PodSelector’ and ‘PolicyTypes’ are intentionally excluded because they are managed by the controller to ensure strict isolation and default-deny posture. WARNING: This policy enforces a strict “Default Deny” ingress posture. If your Pod uses sidecars (e.g., Istio proxy, monitoring agents) that listen on their own ports, the NetworkPolicy will BLOCK traffic to them by default. You MUST explicitly allow traffic to these sidecar ports using ‘Ingress’, otherwise the sidecars may fail health checks. | | Optional: {} |
| `networkPolicyManagement` *NetworkPolicyManagement* | networkPolicyManagement defines whether the controller manages the NetworkPolicy. Valid values are “Managed” (default) or “Unmanaged”. | Managed | Enum: \[Managed Unmanaged] Optional: {} |
| `envVarsInjectionPolicy` *EnvVarsInjectionPolicy* | envVarsInjectionPolicy allows a SandboxClaim to inject or override environment variables defined in the template. If set to Disallowed, the SandboxClaim will be rejected if it specifies any environment variables. | Disallowed | Enum: \[Allowed Overrides Disallowed] Optional: {} |

#### SandboxTemplateStatus

SandboxTemplateStatus defines the observed state of Sandbox.

*Appears in:*

* SandboxTemplate

#### SandboxWarmPool

SandboxWarmPool is the Schema for the sandboxwarmpools API.

| Field | Description | Default | Validation |
| --- | --- | --- | --- |
| `apiVersion` *string* | `extensions.agents.x-k8s.io/v1alpha1` | | |
| `kind` *string* | `SandboxWarmPool` | | |
| `kind` *string* | Kind is a string value representing the REST resource this object represents. Servers may infer this from the endpoint the client submits requests to. Cannot be updated. In CamelCase. More info: <https://git.k8s.io/community/contributors/devel/sig-architecture/api-conventions.md#types-kinds> | | Optional: {} |
| `apiVersion` *string* | APIVersion defines the versioned schema of this representation of an object. Servers should convert recognized schemas to the latest internal value, and may reject unrecognized values. More info: <https://git.k8s.io/community/contributors/devel/sig-architecture/api-conventions.md#resources> | | Optional: {} |
| `metadata` *[ObjectMeta](https://kubernetes.io/docs/reference/generated/kubernetes-api/v/#objectmeta-v1-meta)* | Refer to Kubernetes API documentation for fields of `metadata`. | | Optional: {} |
| `spec` *SandboxWarmPoolSpec* | spec defines the desired state of SandboxWarmPool | | Required: {} |
| `status` *SandboxWarmPoolStatus* | status defines the observed state of SandboxWarmPool | | Optional: {} |

#### SandboxWarmPoolSpec

SandboxWarmPoolSpec defines the desired state of SandboxWarmPool.

*Appears in:*

* SandboxWarmPool

| Field | Description | Default | Validation |
| --- | --- | --- | --- |
| `replicas` *integer* | replicas is the desired number of sandboxes in the pool. This field is controlled by an HPA if specified. | | Minimum: 0 Required: {} |
| `sandboxTemplateRef` *SandboxTemplateRef* | sandboxTemplateRef - name of the SandboxTemplate to be used for creating a Sandbox Warning: Any change to the json tag “sandboxTemplateRef” must be synchronized with the TemplateRefField constant. | | Required: {} |
| `updateStrategy` *SandboxWarmPoolUpdateStrategy* | updateStrategy - strategy for updating the SandboxWarmPool pods based on sandboxTemplateRef name change or underlying template changes | | Optional: {} |

#### SandboxWarmPoolStatus

SandboxWarmPoolStatus defines the observed state of SandboxWarmPool.

*Appears in:*

* SandboxWarmPool

| Field | Description | Default | Validation |
| --- | --- | --- | --- |
| `replicas` *integer* | replicas is the total number of sandboxes in the pool. | | Optional: {} |
| `readyReplicas` *integer* | readyReplicas is the total number of sandboxes in the pool that are in a ready state. | | Optional: {} |
| `selector` *string* | selector is the label selector used to find the pods in the pool. | | Optional: {} |

#### SandboxWarmPoolUpdateStrategy

SandboxWarmPoolUpdateStrategy defines the update strategy for the SandboxWarmPool.

*Appears in:*

* SandboxWarmPoolSpec

| Field | Description | Default | Validation |
| --- | --- | --- | --- |
| `type` *SandboxWarmPoolUpdateStrategyType* | type indicates the type of the SandboxWarmPoolUpdateStrategy. Default is OnReplenish. | OnReplenish | Enum: \[Recreate OnReplenish] Optional: {} |

#### SandboxWarmPoolUpdateStrategyType

*Underlying type:* *string*

SandboxWarmPoolUpdateStrategyType is a string enumeration type that enumerates all possible update strategies for the SandboxWarmPool controller.

*Validation:*

* Enum: \[Recreate OnReplenish]

*Appears in:*

* SandboxWarmPoolUpdateStrategy

| Field | Description |
| --- | --- |
| `Recreate` | RecreateSandboxWarmPoolUpdateStrategyType indicates that stale pods are deleted immediately to ensure the pool only contains fresh pods. Note: This applies to PodTemplate spec changes only. Changes to annotations or labels in the template do not trigger recreate. |
| `OnReplenish` | OnReplenishSandboxWarmPoolUpdateStrategyType indicates that stale pods are only replaced when they are manually deleted or when these stale pods are adopted by sandboxclaims and hence replaced by fresh pods. |

#### ShutdownPolicy

*Underlying type:* *string*

ShutdownPolicy describes the policy for shutting down the underlying Sandbox when the SandboxClaim expires.

*Validation:*

* Enum: \[Delete DeleteForeground Retain]

*Appears in:*

* Lifecycle

| Field | Description |
| --- | --- |
| `Delete` | ShutdownPolicyDelete deletes the SandboxClaim (and cascadingly the Sandbox) when expired. |
| `DeleteForeground` | ShutdownPolicyDeleteForeground deletes the SandboxClaim when expired using foreground cascade deletion. The claim remains in the API (with a deletionTimestamp) until its underlying Sandbox and Pod are fully terminated. This allows external systems to observe shutdown progress by checking whether the claim still exists. |
| `Retain` | ShutdownPolicyRetain keeps the SandboxClaim when expired (Status will show Expired). The underlying SandboxClaim resources (Sandbox, Pod, Service) are deleted to save resources, but the SandboxClaim object itself remains. |

#### WarmPoolPolicy

*Underlying type:* *string*

WarmPoolPolicy describes the policy for using warm pools. It can be one of the following:

* “none”: Do not use any warm pool, always create fresh sandboxes
* “default”: Select from all available warm pools that match the template (default)
* A warm pool name: Select only from the specified warm pool (e.g., “fast-pool”, “secure-pool”)

*Appears in:*

* SandboxClaimSpec

| Field | Description |
| --- | --- |
| `none` | WarmPoolPolicyNone indicates that no warm pool should be used. A fresh sandbox will always be created. |
| `default` | WarmPoolPolicyDefault indicates the default behavior: select from all available warm pools that match the template. This is the default behavior if warmpool is not specified. |

Last modified May 7, 2026: [add api documentation (#247) (c435b15)](https://github.com/kubernetes-sigs/agent-sandbox/commit/c435b15a3b02202fbea106ddff969b562bafd1e5)

---

Powered by [curl.md](https://curl.md)

## cta.description

Narrow results with objective:

## cta.commands

| command                                                                     | description               |
|-----------------------------------------------------------------------------|---------------------------|
| curl.md https://agent-sandbox.sigs.k8s.io/docs/api/ --objective <objective> | focus on a specific topic |
