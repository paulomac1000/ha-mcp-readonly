from pathlib import Path

path = Path(".github/review-fix.py")
text = path.read_text(encoding="utf-8")
old = '    return segment.replace("SECURITY_CONTEXT", "context")\n'
new = (
    '    segment = segment.replace("SECURITY_CONTEXT", "context")\n'
    '    return segment.replace(\n'
    '        "context = security_context or context",\n'
    '        "context = security_context or SECURITY_CONTEXT",\n'
    '    )\n'
)
if text.count(old) != 3:
    raise RuntimeError(f"expected three SECURITY_CONTEXT segment rewrites, got {text.count(old)}")
text = text.replace(old, new)
old_path = '            parsed.append(Path(file_path).name)\n'
new_path = '            parsed.append(str(file_path).replace("\\\\", "/").rsplit("/", 1)[-1])\n'
if text.count(old_path) != 1:
    raise RuntimeError("test_config recorder patch site changed")
text = text.replace(old_path, new_path)
path.write_text(text, encoding="utf-8")
