"""Domain errors and their HTTP mapping.

The status codes below are the contract the acceptance criteria are written
against:

===============================  ======  ==================================
Condition                        Status  ``error`` code
===============================  ======  ==================================
Malformed / empty input          400     ``invalid_request``
No membership record, or a       403     ``membership_required``
membership that is not an
active Mangasm+ subscription
Unknown mix                      404     ``mix_not_found``
User already voted for the mix   409     ``duplicate_vote``
Mix voting window has closed     409     ``voting_closed``
Supabase unavailable after       500     ``upstream_unavailable``
retries
===============================  ======  ==================================
"""

from __future__ import annotations

MEMBERSHIP_REQUIRED_DETAIL = "M+ Membership Required"


class VotingError(Exception):
    """Base class for errors that map onto a deterministic HTTP response."""

    status_code = 500
    error_code = "internal_error"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class InvalidInputError(VotingError):
    status_code = 400
    error_code = "invalid_request"


class MembershipRequiredError(VotingError):
    """Raised when the membership record is missing or not an active Mangasm+."""

    status_code = 403
    error_code = "membership_required"

    def __init__(self, detail: str = MEMBERSHIP_REQUIRED_DETAIL) -> None:
        super().__init__(detail)


class MixNotFoundError(VotingError):
    status_code = 404
    error_code = "mix_not_found"


class DuplicateVoteError(VotingError):
    """Raised when the same user votes twice for the same mix."""

    status_code = 409
    error_code = "duplicate_vote"

    def __init__(self, detail: str = "Vote already recorded for this mix") -> None:
        super().__init__(detail)


class VotingClosedError(VotingError):
    """Raised when the mix exists but its voting window has closed."""

    status_code = 409
    error_code = "voting_closed"

    def __init__(self, detail: str = "Voting is closed for this mix") -> None:
        super().__init__(detail)


class UpstreamUnavailableError(VotingError):
    """Raised once the retry budget for a Supabase call is exhausted."""

    status_code = 500
    error_code = "upstream_unavailable"

    def __init__(self, detail: str = "Vote could not be recorded, please retry") -> None:
        super().__init__(detail)
