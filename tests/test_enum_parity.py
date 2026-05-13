"""Guard against drift between Python Literal types and vendored schema $defs."""

from typing import get_args

from bh_fastapi_audit._types import ActionType, DataClassification, OutcomeStatus
from bh_fastapi_audit.schema import load_schema


class TestEnumParity:
    def test_action_type_matches_schema(self):
        schema = load_schema("1.1")
        assert set(get_args(ActionType)) == set(schema["$defs"]["ActionType"]["enum"])

    def test_outcome_status_matches_schema(self):
        schema = load_schema("1.1")
        assert set(get_args(OutcomeStatus)) == set(schema["$defs"]["OutcomeStatus"]["enum"])

    def test_data_classification_matches_schema(self):
        schema = load_schema("1.1")
        assert set(get_args(DataClassification)) == set(
            schema["$defs"]["DataClassification"]["enum"]
        )
