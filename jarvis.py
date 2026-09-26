from pathlib import Path
import marshal

compiled_copy = Path(__file__).with_name("__pycache__") / "jarvis.cpython-314.pyc"
with compiled_copy.open("rb") as compiled_file:
    compiled_file.read(16)  # Skip the CPython bytecode header.
    program = marshal.load(compiled_file)

exec(program, {"__name__": "__main__", "__file__": str(Path(__file__))})