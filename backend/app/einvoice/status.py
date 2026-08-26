"""ETTN / envelope state machine (spec §5).

::

                  submit()            query_status() (poll)
       (yok) ───────────────▶ PENDING ───────────────▶ SENT ──┬──▶ ACCEPTED
                                 │                            │
                                 │ gönderim reddi             └──▶ REJECTED
                                 ▼
                              FAILED  (external_id yok)

Invariants enforced here, not by convention:

* ``ACCEPTED`` / ``REJECTED`` are terminal — nothing moves a document out of them.
* Status only ever moves forward. A late/duplicate poll cannot walk ``SENT``
  back to ``PENDING``.
* ``FAILED`` means *the submission never landed, there is no ETTN*, so it is a
  **submit-time terminal only** and is reachable only while the document has no
  ETTN (``NONE``/``FAILED``). A failing **status query** never borrows it —
  that would be self-contradictory, since the query carries the very ETTN whose
  existence ``FAILED`` denies. Query failures use :data:`UNRESOLVED` instead.
* Nothing in this module can produce ``ACCEPTED`` on its own: it is returned
  only when the provider's own answer maps to it via
  :data:`~app.einvoice.endpoints.PROVIDER_STATUS_ALIASES`.
"""

from __future__ import annotations

from typing import Any

from .endpoints import PROVIDER_STATUS_ALIASES


NONE = "NONE"
PENDING = "PENDING"
SENT = "SENT"
ACCEPTED = "ACCEPTED"
REJECTED = "REJECTED"
FAILED = "FAILED"

#: NOT a document state — a *query outcome*. ``FAILED`` means "the submission
#: never landed, there is no ETTN" (spec §5), so a failing **status query** must
#: not borrow it: the envelope is still at the provider and its ETTN is still
#: valid. ``UNRESOLVED`` says "we could not determine the status right now";
#: it carries the ETTN, never becomes a stored document state, and
#: :func:`advance_status` leaves the document exactly where it was.
UNRESOLVED = "UNRESOLVED"

TERMINAL: frozenset[str] = frozenset({ACCEPTED, REJECTED})
KNOWN: frozenset[str] = frozenset({NONE, PENDING, SENT, ACCEPTED, REJECTED, FAILED})

#: The only states a *live envelope* can be reported in. A status query is asked
#: about a specific ETTN, so its answer may only be one of these four. The other
#: two members of :data:`KNOWN` describe a document with **no** envelope —
#: ``NONE`` ("never sent") and ``FAILED`` ("the send never landed, there is no
#: ETTN") — so a query answering with either contradicts the ETTN it was asked
#: about. Callers map anything outside this set to :data:`UNRESOLVED`.
QUERYABLE: frozenset[str] = frozenset({PENDING, SENT, ACCEPTED, REJECTED})

#: Forward-only ordering. ``NONE`` and ``FAILED`` share rank 0: both mean "no
#: live envelope at the provider", and a retry of ``submit()`` is legitimate.
_RANK: dict[str, int] = {NONE: 0, FAILED: 0, PENDING: 1, SENT: 2, ACCEPTED: 3, REJECTED: 3}

_NORMALISE = str.maketrans({"İ": "I", "ı": "I", "Ş": "S", "ş": "S", "Ğ": "G", "ğ": "G",
                            "Ü": "U", "ü": "U", "Ö": "O", "ö": "O", "Ç": "C", "ç": "C",
                            " ": "", "-": "", "_": ""})


def map_provider_status(value: Any, extra: dict[str, str] | None = None) -> str | None:
    """Map a provider status token to an internal status, or ``None``.

    ``None`` means *the provider did not tell us a status we understand* — an
    empty body, an unmapped code, a transport-level "OK". The caller must treat
    that as unknown and never as acceptance.

    ``extra`` is a provider-specific table consulted **before** the shared one.
    It exists because some codes are only meaningful for one provider: İzibiz
    answers e-Arşiv queries with bare numbers (``105``, ``130``) that would be
    meaningless — and dangerous — as global aliases.
    """
    if value is None:
        return None
    token = str(value).strip().translate(_NORMALISE).upper()
    if not token:
        return None
    if token in KNOWN:
        return token
    if extra:
        mapped = extra.get(token)
        if mapped:
            return mapped
    return PROVIDER_STATUS_ALIASES.get(token)


def advance_status(current: Any, incoming: Any) -> str:
    """Apply one provider-reported status to the current one, forward-only."""
    current_status = str(current or NONE).strip().upper() or NONE
    incoming_status = str(incoming or NONE).strip().upper() or NONE

    if incoming_status == UNRESOLVED:
        # A failed/undeterminable query is not a transition at all.
        return current_status
    if current_status in TERMINAL:
        return current_status
    if incoming_status not in KNOWN:
        # Statuses outside the machine (e.g. the router's legacy "ERROR") stay
        # observable, but still cannot overwrite a terminal state.
        return incoming_status
    if current_status not in KNOWN:
        return incoming_status
    if incoming_status == FAILED and _RANK[current_status] > 0:
        # A document that already has an ETTN cannot be walked back to FAILED by
        # a failing poll; the failure is reported through ``error`` instead.
        return current_status
    if _RANK[incoming_status] < _RANK[current_status]:
        return current_status
    return incoming_status
