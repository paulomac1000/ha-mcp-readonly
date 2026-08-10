from pathlib import Path

path = Path(".github/review-fix.py")
text = path.read_text(encoding="utf-8")
old = '    return segment.replace("SECURITY_CONTEXT", "context")\n'
new = (
    '    segment = segment.replace("SECURITY_CONTEXT", "context")\n'
    '    segment = segment.replace(\n'
    '        "context = content[match_start:match_end].replace(\\"\\\\n\\", \\" \\").strip()",\n'
    '        "match_context = content[match_start:match_end].replace(\\"\\\\n\\", \\" \\").strip()",\n'
    '    )\n'
    '    segment = segment.replace(\n'
    '        "{\\"position\\": match.start(), \\"context\\": context}",\n'
    '        "{\\"position\\": match.start(), \\"context\\": match_context}",\n'
    '    )\n'
    '    return segment.replace(\n'
    '        "context = security_context or context",\n'
    '        "context = security_context or SECURITY_CONTEXT",\n'
    '    )\n'
)
if text.count(old) != 3:
    raise RuntimeError(f"expected three SECURITY_CONTEXT segment rewrites, got {text.count(old)}")
text = text.replace(old, new)
old_path = '            parsed.append(Path(file_path).name)\n'
new_path = '            parsed.append(str(file_path).rsplit("/", 1)[-1])\n'
if text.count(old_path) != 1:
    raise RuntimeError("test_config recorder patch site changed")
text = text.replace(old_path, new_path)
old_context = '''    security_context = SecurityContext(\n        allowed_directories=[Path(config_path or "/config")],\n        max_file_size=10 * 1024 * 1024,\n        max_depth=20,\n    )\n    default_root = str(security_context.allowed_directories[0])\n'''
new_context = '''    security_context = (\n        SecurityContext(\n            allowed_directories=[Path(config_path)],\n            max_file_size=10 * 1024 * 1024,\n            max_depth=20,\n        )\n        if config_path\n        else SECURITY_CONTEXT\n    )\n    default_root = str(security_context.allowed_directories[0])\n'''
if text.count(old_context) != 1:
    raise RuntimeError("filesystem registration replacement site changed")
text = text.replace(old_context, new_context)
path.write_text(text, encoding="utf-8")