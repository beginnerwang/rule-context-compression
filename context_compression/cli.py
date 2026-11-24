"""File/stdin entry points. stdout is the exact model view; diagnostics go to stderr."""
import argparse
import json
import sys
from pathlib import Path

from .json_codec import compact_json
from .reading import compress_reading
from .sources import OriginalStore, restore
from .tokens import TokenCounter
from .tool_output import compress_tool, decode_template


def read_input(path):
    data = sys.stdin.buffer.read() if path == "-" else Path(path).read_bytes()
    return data.decode("utf-8")


def range_pair(value):
    try:
        first, last = (int(s) for s in value.split(":"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected two integers separated by a colon") from exc
    return first, last


def main(argv=None):
    parser = argparse.ArgumentParser(description="Rule-based document and tool-output compression")
    commands = parser.add_subparsers(dest="command", required=True)
    read = commands.add_parser("read", help="select a verbatim elastic 4K evidence packet")
    tool = commands.add_parser("tool", help="compress a log, a JSON result or a JSONL event stream")
    recovery = commands.add_parser("restore", help="read the original or a source range")
    unpack = commands.add_parser("decode-template", help="rebuild the original text from a template view")
    for group in (read, tool, unpack):
        group.add_argument("--input", default="-", help="UTF-8 input file or - for stdin")
    for group in (read, tool, recovery):
        group.add_argument("--store", default="runtime/originals", help="directory for original inputs")
    read.add_argument("--question", required=True)
    read.add_argument("--options", default="{}", help="JSON object of option labels and texts")
    tool.add_argument("--question", default="")
    tool.add_argument("--budget", type=int, default=1024)
    tool.add_argument("--format", choices=["auto", "log", "json", "jsonl"], default="auto")
    tool.add_argument("--mode", choices=["fused", "baseline"], default="fused",
                      help="enable fused rules (default) or compare the original rules")
    recovery.add_argument("source_id")
    selection = recovery.add_mutually_exclusive_group()
    selection.add_argument("--lines", type=range_pair, help="inclusive original line range, e.g. 5:8")
    selection.add_argument("--chars", type=range_pair, help="zero-based Unicode character range START:END, end excluded")
    selection.add_argument("--pointer", help="JSON Pointer into the stored original")
    args = parser.parse_args(argv)
    try:
        if args.command == "decode-template":
            view = decode_template(read_input(args.input))
        elif args.command == "restore":
            view = restore(OriginalStore(args.store), args.source_id, lines=args.lines, pointer=args.pointer, chars=args.chars)
        else:
            store = OriginalStore(args.store)
            raw = read_input(args.input)
            counter = TokenCounter()
            if args.command == "tool":
                metadata = compress_tool(raw, args.question, args.budget, counter, kind=args.format,
                                         mode=args.mode, store=store)
                view = metadata.pop("text")
            else:
                metadata = compress_reading(raw, args.question, json.loads(args.options), counter)
                view = compact_json(metadata.pop("evidence"))
                metadata.update(source_id=store.save(raw), input_tokens=counter.text(raw))
            print(compact_json(metadata), file=sys.stderr)
        sys.stdout.buffer.write(view.encode("utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        print(f"context-compress: {exc}", file=sys.stderr)
        return 2
    return 0
