"""The information a node receives when it executes."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lanereach.automation.engine.services import EngineServices
    from lanereach.automation.workflows.models import Workflow
    from lanereach.domain.companies.models import Company
    from lanereach.domain.contacts.models import Contact
    from lanereach.domain.offices.models import Office
    from lanereach.tenants.models import Tenant


@dataclass
class EnrollmentSubject:
    """The rows an enrollment step acts on, loaded once per tick."""

    tenant: Tenant
    workflow: Workflow
    contact: Contact
    company: Company | None
    office: Office | None


@dataclass
class ExecutionContext:
    """One step of one enrollment.

    Attributes:
        tenant_id: Tenant the enrollment belongs to.
        enrollment_id: The enrollment being advanced.
        contact_id: The enrolled contact.
        node_id: ID of the node executing (as in the workflow graph).
        now: Current time from the `Clock` (simulated during test-runs).
        facts: Flat condition facts, for example `{"contact.country": "AE"}`.
        variables: Mutable enrollment variables (`enrollment.variables.*`, `reply`, `lead`).
        step_index: Position of this step in the enrollment's history.
        services: Engine services for side-effecting nodes (None in pure unit tests).
        subject: Loaded contact/company/office/tenant/workflow (None in pure unit tests).
        guardrail_decisions: Filled by email nodes; stored on the step.
        output: Extra step output stored on the step (for example the sent Message-ID).
    """

    tenant_id: uuid.UUID
    enrollment_id: uuid.UUID
    contact_id: uuid.UUID
    node_id: str
    now: datetime
    facts: Mapping[str, Any] = field(default_factory=dict)
    variables: dict[str, Any] = field(default_factory=dict)
    step_index: int = 0
    services: EngineServices | None = None
    subject: EnrollmentSubject | None = None
    guardrail_decisions: list[dict[str, Any]] = field(default_factory=list)
    output: dict[str, Any] = field(default_factory=dict)

    @property
    def idempotency_key(self) -> str:
        """`enrollment_id:step_index`, passed to providers so a re-run never duplicates."""
        return f"{self.enrollment_id}:{self.step_index}"

    def require_services(self) -> EngineServices:
        """Return the engine services.

        Returns:
            The services.

        Raises:
            RuntimeError: If the node runs outside the engine.
        """
        if self.services is None:
            raise RuntimeError("this node needs the engine services")
        return self.services

    def require_subject(self) -> EnrollmentSubject:
        """Return the loaded enrollment subject.

        Returns:
            The subject.

        Raises:
            RuntimeError: If the node runs outside the engine.
        """
        if self.subject is None:
            raise RuntimeError("this node needs the enrollment subject")
        return self.subject
