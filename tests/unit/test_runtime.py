import pytest

from twin.bots.runtime import ProcessRuntime, launch_runtime


def test_launch_runtime_resolves_process() -> None:
    assert isinstance(launch_runtime("process"), ProcessRuntime)


@pytest.mark.parametrize(
    "kind", ["job", "kata", "microvm"], ids=["job-tier", "kata-tier", "microvm-tier"]
)
def test_launch_runtime_rejects_unbuilt_tiers(kind: str) -> None:
    with pytest.raises(ValueError, match="unknown bot runtime"):
        launch_runtime(kind)
