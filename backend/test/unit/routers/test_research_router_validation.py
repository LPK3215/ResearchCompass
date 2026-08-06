import pytest
from pydantic import ValidationError

from server.routers.research_router import (
    CreateResearchProjectMilestoneRequest,
    CreateResearchProjectTaskRequest,
    PaperMetadataUpdate,
)


@pytest.mark.parametrize(
    "request_model",
    [CreateResearchProjectMilestoneRequest, CreateResearchProjectTaskRequest],
)
def test_project_plan_create_rejects_whitespace_only_title(request_model):
    with pytest.raises(ValidationError):
        request_model(title="   ")


def test_paper_metadata_update_rejects_null_title_as_validation_error():
    with pytest.raises(ValidationError):
        PaperMetadataUpdate(title=None)
