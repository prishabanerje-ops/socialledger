import pytest
from pydantic import ValidationError

from socialledger.schema import Availability, MetricValue, Source


def test_available_metric_requires_value() -> None:
    with pytest.raises(ValidationError):
        MetricValue(availability=Availability.AVAILABLE)


MISSING = [a for a in Availability if a is not Availability.AVAILABLE]


@pytest.mark.parametrize("availability", MISSING)
def test_missing_metric_cannot_be_zero_filled(availability: Availability) -> None:
    with pytest.raises(ValidationError, match="no zero-filling"):
        MetricValue(value=0, availability=availability)


def test_helpers() -> None:
    mv = MetricValue.of(5, Source.CSV_IMPORT)
    assert mv.value == 5.0 and mv.is_available and mv.source is Source.CSV_IMPORT
    gap = MetricValue.missing(Availability.NOT_AUTHORIZED, "needs approval")
    assert gap.value is None and not gap.is_available and gap.note == "needs approval"
