# Routing Policy Specification

## Core rule

**All node-originated follow-up traffic must return to Gate.**

## Allowed patterns

### Client ingress
```text
client -> gate
Orchestrator or worker follow-up
node -> gate
Gate dispatch
gate -> worker
Forbidden pattern
node-a -> node-b
No worker or orchestrator may directly target another worker node.

Required packet semantics
Node-origin packet
provenance.origin_kind == "node"

address.source_node == local node

address.destination_node == "gate"

provenance.original_source_node == local node

Gate dispatch packet
provenance.origin_kind == "gate"

provenance.resolved_by_gate == true

address.source_node == "gate"

address.destination_node == resolved worker node

Policy enforcement points
SDK Gate client

Gate ingress validator

Gate routing policy validator

architecture tests


```markdown
