from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .cases import load_case_set
from .config import Settings
from .contracts import Contracts
from .mcp_gateway import connect_gateway
from .submission import package_submission, validate_artifacts
from .trace import TraceWriter
from .workflow import solve_case


def _root(value: str) -> Path:
    return Path(value).resolve()


async def _show_tools(root: Path) -> None:
    settings = Settings.load(root)
    contracts = Contracts(root / "contracts" / "schemas")
    async with connect_gateway(settings.mcp_endpoint, settings.team_api_key, contracts) as gateway:
        for tool in await gateway.list_tools():
            print(tool)


def _clean_trace_to_completed(trace_path: Path, completed: set[str]) -> None:
    if not trace_path.exists():
        return
    try:
        raw_text = trace_path.read_text(encoding="utf-8")
        lines = [line for line in raw_text.splitlines() if line.strip()]
    except Exception:
        return
    valid_lines = []
    for line in lines:
        try:
            ev = json.loads(line)
            if ev.get("case_id") in completed:
                valid_lines.append(line)
        except Exception:
            pass
    trace_path.write_text(
        "\n".join(valid_lines) + ("\n" if valid_lines else ""), encoding="utf-8"
    )


async def _run(root: Path, clean: bool = False) -> None:
    settings = Settings.load(root)
    case_set = load_case_set(root)
    contracts = Contracts(root / "contracts" / "schemas")
    output_root = root / "outputs"
    trace_path = root / "traces" / "trace.jsonl"
    output_root.mkdir(parents=True, exist_ok=True)
    trace_path.parent.mkdir(parents=True, exist_ok=True)

    if clean:
        for stale in output_root.glob("*.json"):
            stale.unlink()
        trace_path.unlink(missing_ok=True)

    completed = {
        p.stem
        for p in output_root.glob("*.json")
        if p.name != ".gitkeep" and p.stat().st_size > 0
    }
    _clean_trace_to_completed(trace_path, completed)
    trace = TraceWriter(trace_path, contracts)

    for case_id in case_set.case_ids:
        if case_id in completed:
            continue
        case = case_set.cases[case_id]
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                async with connect_gateway(
                    settings.mcp_endpoint, settings.team_api_key, contracts
                ) as gateway:
                    trace.emit(case_id=case_id, event_type="case_received", actor="coordinator")
                    output = await solve_case(case, gateway, trace)
                    contracts.validate_output(output, f"outputs/{case_id}.json")
                    if output.get("case_id") != case_id:
                        raise ValueError(f"solver returned a mismatched case_id for {case_id}")
                    target = output_root / f"{case_id}.json"
                    temporary = target.with_suffix(".json.tmp")
                    temporary.write_text(
                        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                    )
                    temporary.replace(target)
                    trace.emit(case_id=case_id, event_type="case_finalized", actor="coordinator")
                    completed.add(case_id)
                    print(f"[{len(completed)}/{len(case_set.case_ids)}] Completed {case_id}")
                    break
            except Exception as exc:
                _clean_trace_to_completed(trace_path, completed)
                if attempt < max_retries:
                    print(
                        f"Warning: {case_id} failed on attempt {attempt} ({exc}). Retrying in 2s..."
                    )
                    await asyncio.sleep(2)
                else:
                    raise


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Day09 L3B student workflow")
    result.add_argument("--root", default=".", help="repository root (default: current directory)")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-inputs", help="validate case-set.json and all 100 inputs")
    commands.add_parser("mcp-tools", help="authenticate and list discovered MCP tools")
    run_parser = commands.add_parser("run", help="run the implemented workflow for all cases")
    run_parser.add_argument(
        "--clean", action="store_true", help="clear existing outputs and start from scratch"
    )
    commands.add_parser("validate", help="validate outputs and observable trace")
    package = commands.add_parser("package", help="validate and build the submission ZIP")
    package.add_argument("--output", default="dist/submission.zip")
    return result


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = parser().parse_args()
    root = _root(args.root)
    try:
        if args.command == "validate-inputs":
            case_set = load_case_set(root)
            print(
                f"OK: {case_set.variant_id} / {case_set.version} / "
                f"{len(case_set.case_ids)} cases"
            )
        elif args.command == "mcp-tools":
            asyncio.run(_show_tools(root))
        elif args.command == "run":
            asyncio.run(_run(root, clean=args.clean))
        elif args.command == "validate":
            case_set = load_case_set(root)
            contracts = Contracts(root / "contracts" / "schemas")
            _, trace = validate_artifacts(root, case_set, contracts)
            print(f"OK: {len(case_set.case_ids)} outputs / {len(trace)} trace events")
        elif args.command == "package":
            destination = package_submission(root, root / args.output)
            print(f"OK: {destination}")
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
