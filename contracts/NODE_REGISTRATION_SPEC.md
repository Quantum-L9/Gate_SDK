# Node Registration Specification

## Purpose

Nodes register with Gate so Gate can resolve actions to healthy runtime instances.

## Registration endpoint

```text
POST /v1/admin/register
Payload shape
Top-level JSON object keyed by node name:

{
  "score": {
    "internal_url": "http://score:8000",
    "supported_actions": ["score"],
    "priority_class": "P1",
    "max_concurrent": 25,
    "health_endpoint": "/v1/health",
    "timeout_ms": 15000,
    "metadata": {
      "version": "1.2.3",
      "type": "worker",
      "generated_by": "constellation-node-sdk"
    }
  }
}
Required fields
internal_url

supported_actions

Optional fields
priority_class

max_concurrent

health_endpoint

timeout_ms

metadata

Registration rules
node names are normalized to lowercase

supported actions must be non-empty

internal URL must be absolute

registration may be rejected if overwrite is false and node exists

Gate is authoritative for activation and health state


```markdown
