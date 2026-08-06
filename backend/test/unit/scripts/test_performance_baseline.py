from __future__ import annotations

from scripts.performance_baseline import Sample, _percentile, _summary


def test_percentile_interpolates_without_mutating_samples():
    values = [30.0, 10.0, 20.0]
    assert _percentile(values, 0.5) == 20.0
    assert values == [30.0, 10.0, 20.0]


def test_summary_separates_transport_errors_and_status_counts():
    result = _summary(
        [
            Sample(10.0, 200),
            Sample(20.0, 200),
            Sample(5.0, 500, "unexpected status 500"),
            Sample(0.0, None, "ReadTimeout"),
        ]
    )

    assert result["requests"] == 4
    assert result["successful_samples"] == 2
    assert result["errors"] == 2
    assert result["status_counts"] == {"200": 2, "500": 1, "transport_error": 1}
    assert result["latency_ms"]["p95"] == 19.5
