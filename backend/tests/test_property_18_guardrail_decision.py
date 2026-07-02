"""Property-based test for the adapter accept/reject guardrail decision (design Property 18).

# Feature: handwriting-notes-digitizer, Property 18: Adapter accept/reject guardrail decision

This module verifies **Property 18**:

    For any (adapter_cer, base_cer, adapter_wer, base_wer) tuple, the guardrail
    function accepts the adapter if and only if:

        adapter_cer <= base_cer + 0.01   (CER tolerance: 1.0 percentage point)
        AND
        adapter_wer <= base_wer + 0.02   (WER tolerance: 2.0 percentage points)

    It rejects (returning REJECTED_CER or REJECTED_WER) when either condition
    fails.  When the CER constraint is violated the result is always REJECTED_CER
    regardless of the WER value.

**Validates: Requirements 6.3, 6.4**

Properties tested
-----------------
1. Accepted iff both CER and WER constraints are satisfied.
2. Rejected with REJECTED_CER when CER constraint is violated (regardless of WER).
3. Rejected with REJECTED_WER when WER constraint is violated and CER is within tolerance.

Generator strategy
------------------
``guardrail_inputs()`` draws base and adapter CER/WER values as floats in
[0.0, 1.0] without NaN or infinity.  The space naturally covers all four
quadrants: both within tolerance, only CER violated, only WER violated, and
both violated.  Hypothesis will find boundary examples near 0.01 / 0.02 deltas
automatically.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from app.services.personalization_service import GuardrailResult, evaluate_guardrail

# ---------------------------------------------------------------------------
# Constants mirroring the production thresholds (for readability in assertions)
# ---------------------------------------------------------------------------

_CER_TOLERANCE = 0.01   # 1.0 percentage point
_WER_TOLERANCE = 0.02   # 2.0 percentage points


# ---------------------------------------------------------------------------
# Generator strategy
# ---------------------------------------------------------------------------

@st.composite
def guardrail_inputs(draw: st.DrawFn) -> tuple[float, float, float, float]:
    """Generate (adapter_cer, base_cer, adapter_wer, base_wer) tuples.

    All four values are independent floats in [0.0, 1.0] without NaN or
    infinity, covering the full decision space.
    """
    base_cer = draw(
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
    )
    base_wer = draw(
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
    )
    adapter_cer = draw(
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
    )
    adapter_wer = draw(
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
    )
    return adapter_cer, base_cer, adapter_wer, base_wer


# ---------------------------------------------------------------------------
# Property 18: Adapter accept/reject guardrail decision
# ---------------------------------------------------------------------------

# Feature: handwriting-notes-digitizer, Property 18: Adapter accept/reject guardrail decision
@given(inputs=guardrail_inputs())
@settings(max_examples=25)
def test_property_18_guardrail_decision(
    inputs: tuple[float, float, float, float],
) -> None:
    """Property 18: accept iff adapter_cer <= base_cer + 0.01 AND adapter_wer <= base_wer + 0.02.

    Asserts:
    - ACCEPTED when both CER and WER constraints are satisfied.
    - REJECTED_CER when CER constraint is violated (CER check takes priority).
    - REJECTED_WER when WER constraint is violated and CER is within tolerance.

    **Validates: Requirements 6.3, 6.4**
    """
    adapter_cer, base_cer, adapter_wer, base_wer = inputs

    result = evaluate_guardrail(
        adapter_cer=adapter_cer,
        base_cer=base_cer,
        adapter_wer=adapter_wer,
        base_wer=base_wer,
    )

    cer_ok = adapter_cer <= base_cer + _CER_TOLERANCE
    wer_ok = adapter_wer <= base_wer + _WER_TOLERANCE

    if cer_ok and wer_ok:
        # Both constraints satisfied → must accept.
        assert result == GuardrailResult.ACCEPTED, (
            f"Expected ACCEPTED but got {result!r}. "
            f"adapter_cer={adapter_cer}, base_cer={base_cer} "
            f"(diff={adapter_cer - base_cer:.6f}, tol={_CER_TOLERANCE}), "
            f"adapter_wer={adapter_wer}, base_wer={base_wer} "
            f"(diff={adapter_wer - base_wer:.6f}, tol={_WER_TOLERANCE})"
        )
    elif not cer_ok:
        # CER constraint violated → must report REJECTED_CER (regardless of WER).
        assert result == GuardrailResult.REJECTED_CER, (
            f"CER constraint violated but got {result!r} instead of REJECTED_CER. "
            f"adapter_cer={adapter_cer}, base_cer={base_cer} "
            f"(diff={adapter_cer - base_cer:.6f} > tol={_CER_TOLERANCE})"
        )
    else:
        # CER is OK but WER is violated → must report REJECTED_WER.
        assert result == GuardrailResult.REJECTED_WER, (
            f"WER constraint violated (CER OK) but got {result!r} instead of REJECTED_WER. "
            f"adapter_wer={adapter_wer}, base_wer={base_wer} "
            f"(diff={adapter_wer - base_wer:.6f} > tol={_WER_TOLERANCE})"
        )
