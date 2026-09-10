from collections import defaultdict

_values: defaultdict[str, int] = defaultdict(int)


def increment(name: str, amount: int = 1) -> None:
    _values[name] += amount


def render() -> str:
    lines = [f"{name} {value}" for name, value in sorted(_values.items())]
    return "\n".join(lines) + ("\n" if lines else "")
