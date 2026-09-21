"""Purpose-specific model input masking policy."""

from core.masking import MaskingPath
from core.model_contracts import ModelPurpose


ALLOWED_INPUT_PREFIXES: dict[ModelPurpose, tuple[MaskingPath, ...]] = {
    ModelPurpose.PLANNER: (),
    ModelPurpose.AGGREGATOR: (("answer",),),
    ModelPurpose.QA: (("customer_info",), ("bindings",), ("evidence",)),
    ModelPurpose.INPUT_ASSIST: (
        ("customer_info",), ("bindings",), ("current_values",), ("items",), ("changes",),
    ),
    ModelPurpose.ERROR_REVIEW: (("current_form",),),
    ModelPurpose.CHART_INTERPRET: (("chart_facts",),),
    ModelPurpose.ELICIT: (),
    ModelPurpose.SUMMARY: (),
}
