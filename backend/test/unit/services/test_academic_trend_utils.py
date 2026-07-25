"""Unit tests for academic_trend_service pure utility functions."""


from yuxi.services.academic_trend_service import _emerging_keywords, _trend_keywords


class TestTrendKeywords:
    def test_basic_grouping(self):
        rows = [
            (2023, ["GNN", "drug"], 5),
            (2023, ["GNN", "molecule"], 3),
            (2024, ["gnn", "Drug"], 10),
        ]
        result = _trend_keywords(rows)
        assert len(result) == 3

        gnn = next(item for item in result if item["keyword"].casefold() == "gnn")
        assert gnn["total"] == 3
        assert gnn["first_year"] == 2023
        assert gnn["last_year"] == 2024
        assert len(gnn["years"]) == 2

    def test_keyword_normalization_preserves_display(self):
        rows = [(2024, ["Deep Learning"], 0)]
        result = _trend_keywords(rows)
        assert result[0]["keyword"] == "Deep Learning"

    def test_year_none_skipped(self):
        rows = [
            (None, ["GNN"], 5),
            (2024, ["GNN"], 3),
        ]
        result = _trend_keywords(rows)
        assert len(result) == 1
        assert result[0]["total"] == 1

    def test_empty_keywords_ignored(self):
        rows = [(2024, ["  ", ""], 0)]
        result = _trend_keywords(rows)
        assert result == []

    def test_sorted_by_total_desc(self):
        rows = [
            (2024, ["rare"], 0),
            (2023, ["common"], 0),
            (2024, ["common"], 0),
        ]
        result = _trend_keywords(rows)
        assert result[0]["keyword"] == "common"
        assert result[0]["total"] == 2
        assert result[1]["keyword"] == "rare"
        assert result[1]["total"] == 1

    def test_empty_rows(self):
        assert _trend_keywords([]) == []

    def test_dedup_keywords_within_same_row(self):
        rows = [(2024, ["GNN", "GNN", "gnn"], 0)]
        result = _trend_keywords(rows)
        assert len(result) == 1
        assert result[0]["total"] == 1


class TestEmergingKeywords:
    def test_new_keyword_detected(self):
        trends = [
            {
                "keyword": "LLM",
                "total": 3,
                "first_year": 2024,
                "last_year": 2024,
                "years": [{"year": 2024, "count": 3}],
            },
            {
                "keyword": "OldTech",
                "total": 5,
                "first_year": 2020,
                "last_year": 2022,
                "years": [{"year": 2020, "count": 2}, {"year": 2022, "count": 3}],
            },
        ]
        result = _emerging_keywords(trends)
        assert len(result) == 1
        assert result[0]["keyword"] == "LLM"
        assert result[0]["is_new"] is True

    def test_growing_keyword_detected(self):
        trends = [
            {
                "keyword": "GNN",
                "total": 6,
                "first_year": 2021,
                "last_year": 2024,
                "years": [
                    {"year": 2022, "count": 1},
                    {"year": 2024, "count": 5},
                ],
            },
        ]
        result = _emerging_keywords(trends)
        assert len(result) == 1
        assert result[0]["keyword"] == "GNN"
        assert result[0]["is_new"] is False
        assert result[0]["growth"] == 4

    def test_declining_keyword_excluded(self):
        trends = [
            {
                "keyword": "Declining",
                "total": 5,
                "first_year": 2020,
                "last_year": 2024,
                "years": [
                    {"year": 2022, "count": 4},
                    {"year": 2024, "count": 1},
                ],
            },
        ]
        result = _emerging_keywords(trends)
        assert len(result) == 0

    def test_old_only_keyword_excluded(self):
        trends = [
            {
                "keyword": "Recent",
                "total": 3,
                "first_year": 2024,
                "last_year": 2024,
                "years": [{"year": 2024, "count": 3}],
            },
            {
                "keyword": "Ancient",
                "total": 3,
                "first_year": 2018,
                "last_year": 2020,
                "years": [{"year": 2018, "count": 1}, {"year": 2020, "count": 2}],
            },
        ]
        result = _emerging_keywords(trends)
        ancient = [item for item in result if item["keyword"] == "Ancient"]
        assert len(ancient) == 0

    def test_empty_trends(self):
        assert _emerging_keywords([]) == []

    def test_sorted_by_growth_desc(self):
        trends = [
            {
                "keyword": "Slow",
                "total": 4,
                "first_year": 2023,
                "last_year": 2024,
                "years": [{"year": 2022, "count": 1}, {"year": 2024, "count": 2}],
            },
            {
                "keyword": "Fast",
                "total": 5,
                "first_year": 2023,
                "last_year": 2024,
                "years": [{"year": 2022, "count": 1}, {"year": 2024, "count": 4}],
            },
        ]
        result = _emerging_keywords(trends)
        assert result[0]["keyword"] == "Fast"
        assert result[1]["keyword"] == "Slow"
