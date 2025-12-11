# User Story 4: Policy Author - Context-Aware Compliance with Human-in-the-Loop

## Persona

**Riley** - A security engineer building policies for a healthcare company (HIPAA compliance).

## Context

Healthcare has strict rules about PHI (Protected Health Information). Some operations need human approval before proceeding. Riley needs policies that understand conversation context, can pause for approval, and communicate clearly with users.

## Story

> As Riley, I want to build policies that understand conversation history, can pause for human approval on sensitive operations, and communicate decisions to users in plain language, so that our AI usage is HIPAA-compliant without blocking legitimate clinical workflows.

## Scenario

1. Riley creates `HIPAACompliancePolicy` with context-aware rules:
   ```python
   class HIPAACompliancePolicy(ContextAwareJudgePolicy):
       RULES = [
           "Block export of patient data unless conversation establishes clinical necessity",
           "Require human approval for any bulk data operations",
           "Allow individual patient lookups if user is authenticated clinician",
           "Log all PHI access for audit trail"
       ]

       HUMAN_APPROVAL_TRIGGERS = [
           "bulk data export",
           "database schema changes",
           "access to more than 10 patient records"
       ]
   ```
2. Dr. Chen (authenticated clinician) asks Claude to "pull up the latest labs for patient John Doe"
3. The proxy:
   - Checks conversation context: Dr. Chen authenticated, single patient lookup
   - Rule evaluation: individual lookup by clinician → ALLOW
   - Logs PHI access event with clinician ID and patient reference
4. Dr. Chen then asks: "Now export all diabetic patients' A1C trends for the quality report"
5. The proxy:
   - Detects bulk data operation trigger
   - **Pauses the request** and injects message: "This bulk export requires supervisor approval. I've notified your department head. You'll receive a notification when approved. Request ID: REQ-2024-1234"
   - Fires webhook to approval system with request details
   - Stores request in pending state
6. Supervisor approves via approval UI at `localhost:8000/approvals/REQ-2024-1234`
7. The proxy:
   - Resumes the original request
   - Injects confirmation: "Export approved by Dr. Smith. Proceeding with A1C trend export."
   - Completes the operation with full audit logging
8. Riley views the **Compliance Dashboard**:
   - PHI access events by user, time, patient count
   - Pending approvals queue
   - Audit trail for compliance reporting

## Acceptance Criteria

- [ ] Policies can access full conversation history for context-aware decisions
- [ ] Human-in-the-loop approval workflow with pause/resume
- [ ] Approval UI for reviewers to approve/deny pending requests
- [ ] Message injection for user communication (pause notification, approval confirmation)
- [ ] LLM rephrasing of policy decisions into user-friendly language
- [ ] Audit logging of all PHI/sensitive data access
- [ ] User authentication context available to policies
- [ ] Compliance dashboard with filterable audit trail

## Required Features

### Context & Conversation

| Issue | Title | Status | Priority |
|-------|-------|--------|----------|
| `luthien-proxy-5sr` | Conversation context tracking across requests | open | P1 |
| `luthien-proxy-3yp` | Context-aware policy base class | open | P2 |

### Human-in-the-Loop

| Issue | Title | Status | Priority |
|-------|-------|--------|----------|
| `luthien-proxy-rtu` | Human-in-the-loop approval workflow | open | P2 |
| `luthien-proxy-ap2` | Approval queue UI | open | P2 |

### User Communication

| Issue | Title | Status | Priority |
|-------|-------|--------|----------|
| `luthien-proxy-fsb` | Message injection into response stream | open | P1 |
| `luthien-proxy-8gv` | LLM rephrasing of policy decisions | open | P2 |

### Compliance & Audit

| Issue | Title | Status | Priority |
|-------|-------|--------|----------|
| `luthien-proxy-4yh` | Compliance audit dashboard | open | P2 |
| `luthien-proxy-aai` | Escalation tiers with webhook alerts | open | P2 |

### Documentation (Existing)

| Issue | Title | Status | Priority |
|-------|-------|--------|----------|
| `luthien-proxy-np9` | Document data retention policy | open | P2 |
| `luthien-proxy-9a0` | Add security documentation for POLICY_CONFIG | open | P1 |

## Technical Touchpoints

- `policies/context_aware_policy.py`: Base class with conversation history
- `approval/`: Human-in-the-loop approval workflow
- `ui/approvals`: Approval queue UI for reviewers
- `ui/compliance_dashboard`: Audit and compliance reporting
- `streaming/policy_executor`: Request pause/resume mechanism
- `llm/rephraser.py`: User-friendly message generation
- `storage/audit_log`: Compliance audit trail

## Implementation Status

**Overall Progress**: Not Started

### Phase 1: Context-Aware Policy Base
- [ ] Design ContextAwarePolicyBase interface
- [ ] Implement conversation history injection
- [ ] Add user authentication context
- [ ] Test context access patterns

### Phase 2: Approval Workflow
- [ ] Design approval request schema
- [ ] Implement request pause mechanism
- [ ] Store pending requests with full context
- [ ] Implement approval/denial handlers
- [ ] Add resume-after-approval flow

### Phase 3: Approval UI
- [ ] Design approval queue interface
- [ ] Implement pending request list
- [ ] Add approve/deny actions with comments
- [ ] Show request context and conversation history
- [ ] Add notification for new approvals

### Phase 4: Message Injection
- [ ] Implement pause notification injection
- [ ] Implement approval confirmation injection
- [ ] Handle timeout/expiry notifications
- [ ] Test with Claude Code client

### Phase 5: Compliance Dashboard
- [ ] Design audit event schema
- [ ] Implement PHI access logging
- [ ] Build compliance dashboard UI
- [ ] Add export for compliance reporting
- [ ] Implement retention policy enforcement

## Dependencies

```
luthien-proxy-5sr (Conversation context tracking)
    └── luthien-proxy-3yp (Context-aware policy base class)

luthien-proxy-rtu (Human-in-the-loop approval workflow)
    └── luthien-proxy-ap2 (Approval queue UI)

luthien-proxy-fsb (Message injection)
    └── luthien-proxy-8gv (LLM rephrasing)

luthien-proxy-aai (Escalation tiers)
    └── luthien-proxy-4yh (Compliance audit dashboard)
```

## Policy Author Interface

```python
from luthien_proxy.policies.context_aware_policy import ContextAwareJudgePolicy
from luthien_proxy.approval import require_approval

class HIPAACompliancePolicy(ContextAwareJudgePolicy):
    """HIPAA-compliant policy with human-in-the-loop approval."""

    RULES = [
        "Block export of patient data unless clinical necessity established",
        "Allow individual patient lookups for authenticated clinicians",
        "Log all PHI access for audit trail",
    ]

    async def evaluate(self, context: PolicyContext) -> PolicyDecision:
        # Access full conversation history
        history = context.conversation_history
        user = context.authenticated_user

        # Check for bulk data patterns
        if self.is_bulk_data_operation(context.request):
            # Pause and require approval
            return require_approval(
                reason="Bulk data export requires supervisor approval",
                approvers=["supervisor", "compliance_officer"],
                timeout_hours=24,
                audit_category="phi_bulk_export"
            )

        # Single patient lookup - allow with logging
        if self.is_single_patient_lookup(context.request):
            context.audit_log(
                category="phi_access",
                details={"user": user.id, "action": "single_patient_lookup"}
            )
            return PolicyDecision.ALLOW

        # Default to LLM judge evaluation
        return await super().evaluate(context)
```

## Approval Request Schema

```json
{
  "request_id": "REQ-2024-1234",
  "status": "pending",  // pending | approved | denied | expired
  "created_at": "2025-01-15T10:30:00Z",
  "expires_at": "2025-01-16T10:30:00Z",
  "requester": {
    "user_id": "dr.chen",
    "role": "clinician",
    "department": "internal_medicine"
  },
  "request_summary": "Export A1C trends for diabetic patients",
  "policy_triggered": "HIPAACompliancePolicy",
  "rule_triggered": "bulk data export requires approval",
  "conversation_context": {
    "session_id": "sess-abc123",
    "message_count": 5,
    "summary": "Dr. Chen reviewing diabetic patient data for quality report"
  },
  "approvers": ["supervisor", "compliance_officer"],
  "approval": {
    "approved_by": null,
    "approved_at": null,
    "comment": null
  }
}
```

## Notes

- Approval timeout should be configurable per policy
- Expired approvals should auto-deny with notification
- Consider approval delegation for out-of-office scenarios
- Audit logs must be immutable (append-only)
- Compliance dashboard should support date range filtering
- Consider integration with external compliance systems (Epic, Cerner)
- PHI detection should use established patterns (NPI, MRN, SSN formats)
