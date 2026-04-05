try:
    import readline
except ImportError:  # pragma: no cover
    readline = None


def setup_readline(runtime) -> None:
    if readline is None:
        return
    readline.parse_and_bind("tab: complete")
    readline.set_completer_delims(" \t\n")
    readline.set_completer(build_completer(runtime))


def build_completer(runtime):
    def completer(text, state):
        buffer_text = readline.get_line_buffer()
        matches = complete_input(runtime, buffer_text, text)
        if state < len(matches):
            return matches[state]
        return None

    return completer


def complete_input(runtime, buffer_text: str, text: str):
    from .repl_commands import VISIBLE_COMMANDS, safe_split

    if text.startswith("@"):
        path_matches = complete_workspace_paths(runtime, text[1:])
        return ["@" + m for m in path_matches]

    stripped = buffer_text.lstrip()
    if not stripped or (stripped.startswith("/") and " " not in stripped):
        return [item for item in VISIBLE_COMMANDS if item.startswith(text)]

    if stripped.startswith("/cat "):
        return complete_workspace_paths(runtime, stripped.split(" ", 1)[1])
    if stripped.startswith("/quick "):
        quick_body = stripped.split(" ", 1)[1]
        if "," not in quick_body:
            quick_target = quick_body.lstrip()
            if quick_target.startswith("@"):
                return ["@" + item for item in complete_workspace_paths(runtime, quick_target[1:])]
            return ["@" + item for item in complete_workspace_paths(runtime, quick_target)]
    if stripped.startswith("/ls "):
        return complete_workspace_paths(runtime, stripped.split(" ", 1)[1])
    if stripped.startswith("/find "):
        args = safe_split(stripped)
        if len(args) >= 3:
            return complete_workspace_paths(runtime, args[2])
    if stripped.startswith("/grep "):
        args = safe_split(stripped)
        if len(args) >= 3:
            return complete_workspace_paths(runtime, args[2])
    if stripped.startswith("/use "):
        return complete_sessions(runtime, stripped.split(" ", 1)[1])
    return []


def complete_workspace_paths(runtime, prefix: str):
    workspace_root = runtime.config.workspace_root
    normalized = prefix.replace("\\ ", " ")

    if prefix == "" or prefix.endswith("/"):
        base_path = (workspace_root / normalized).resolve()
        parent = base_path
        fragment = ""
    else:
        base_path = (workspace_root / normalized).resolve()
        parent = base_path.parent
        fragment = base_path.name

    if not parent.exists() or not parent.is_dir():
        return []

    matches = []
    for child in sorted(parent.iterdir(), key=lambda item: item.name):
        if fragment and not child.name.startswith(fragment):
            continue
        try:
            relative = child.relative_to(workspace_root).as_posix()
        except ValueError:
            continue
        if child.is_dir():
            relative += "/"
        matches.append(escape_completion(relative))
    return matches


def complete_sessions(runtime, prefix: str):
    return [
        session.id
        for session in runtime.session_store.list_sessions()
        if session.id.startswith(prefix)
    ]


def escape_completion(value: str) -> str:
    return value.replace(" ", "\\ ")
